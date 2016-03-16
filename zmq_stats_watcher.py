import sys
import time
import json
import struct
import argparse
import pprint
import os
import signal
import sqlite3
import zmq
import logging

class StatsWatcher():
    def __init__(self, server_addr, stats_port, stats_password):
        self.POLL_TIMEOUT = 1000
        self.stats_password = stats_password
        self.host = "tcp://" + server_addr + ":" + stats_port

        context = zmq.Context()
        self.socket = context.socket( zmq.SUB )
        self.monitor = self.socket.get_monitor_socket( zmq.EVENT_ALL )
        self.socket.plain_username = b'stats'
        self.socket.plain_password = self.stats_password.encode('ascii')
        self.socket.zap_domain = b'stats'

    def _readSocketEvent(self, msg ):
        # NOTE: little endian - hopefully that's not platform specific?
        event_id = struct.unpack( '<H', msg[:2] )[0]
        # NOTE: is it possible I would get a bitfield?
        event_names = {
            zmq.EVENT_ACCEPTED : 'EVENT_ACCEPTED',
            zmq.EVENT_ACCEPT_FAILED : 'EVENT_ACCEPT_FAILED',
            zmq.EVENT_BIND_FAILED : 'EVENT_BIND_FAILED',
            zmq.EVENT_CLOSED : 'EVENT_CLOSED',
            zmq.EVENT_CLOSE_FAILED : 'EVENT_CLOSE_FAILED',
            zmq.EVENT_CONNECTED : 'EVENT_CONNECTED',
            zmq.EVENT_CONNECT_DELAYED : 'EVENT_CONNECT_DELAYED',
            zmq.EVENT_CONNECT_RETRIED : 'EVENT_CONNECT_RETRIED',
            zmq.EVENT_DISCONNECTED : 'EVENT_DISCONNECTED',
            zmq.EVENT_LISTENING : 'EVENT_LISTENING',
            zmq.EVENT_MONITOR_STOPPED : 'EVENT_MONITOR_STOPPED',
        }
        event_name = event_names[ event_id ] if event_id in event_names  else '%d' % event_id
        event_value = struct.unpack( '<I', msg[2:] )[0]
        return ( event_id, event_name, event_value )


    def _checkMonitor(self):
        try:
            event_monitor = self.monitor.recv( zmq.NOBLOCK )
        except zmq.Again:
            #logging.debug( 'again' )
            return

        ( event_id, event_name, event_value ) = self._readSocketEvent( event_monitor )
        event_monitor_endpoint = self.monitor.recv( zmq.NOBLOCK )
        logging.info( 'monitor: %s %d endpoint %s' % ( event_name, event_value, event_monitor_endpoint ) )

    def connect_and_wait_for_end_of_game(self):
        self.socket.connect( self.host )
        self.socket.setsockopt( zmq.SUBSCRIBE, b'' )
        print( 'Connected SUB to %s' % self.host )
        while ( True ):
            event = self.socket.poll( self.POLL_TIMEOUT )
            # check if there are any events to report on the socket
            self._checkMonitor()

            if ( event == 0 ):
                #logging.info( 'poll loop' )
                continue

            while ( True ):
                try:
                    msg = self.socket.recv_json( zmq.NOBLOCK )
                except zmq.error.Again:
                    break
                except Exception as e:
                    logging.info( e )
                    break
                else:
                    if msg['TYPE'] == 'MATCH_REPORT' and not msg['DATA']['ABORTED']:
                        return

