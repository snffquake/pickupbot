import json
import sys
import discord
import asyncio
import random
import valve.source.a2s
import os
import signal
import sqlite3
from discord.errors import HTTPException
from collections import OrderedDict
from multiprocessing import Process
from datetime import datetime
from datetime import timedelta
from discord.utils import find
from zmq_stats_watcher import StatsWatcher


def load_config_to_dict_obj(cfg_file):
    if os.path.isfile(cfg_file):
        config_file = open(cfg_file, "r")
    else:
        print("Config file does not exist.")
        sys.exit(-1)
    try:
        config = json.load(config_file, object_pairs_hook=OrderedDict)
    except ValueError:
        print("Invalid json config file.")
        sys.exit(-1)

    config_file.close()

    return config;


class PickupBot:
    def __init__(self, cfg_file):
        self.pickups = {}
        self.modes = {}
        self.servers = {}
        self.bot_channel = None
        self.account = {}
        self.admin_role = None
        self.db = None
        self.promote_delay_minutes = 15
        self.last_promote_date_exec = None
        config = load_config_to_dict_obj(cfg_file)
        self.init_bot_config(config)
        self.init_pickups(config)
        self.init_db()
        self.client = discord.Client()

        @self.client.event
        @asyncio.coroutine
        def on_ready():
            self.on_read_evnt_handler()

        @self.client.event
        @asyncio.coroutine
        def on_message(msg):
            yield from self.on_message_evnt_handler(msg)

        @self.client.event
        @asyncio.coroutine
        def on_member_update(before, after):
            yield from self.on_member_update_evnt_handler(before, after)

    def init_db(self):
        self.db = sqlite3.connect("users.db");
        c = self.db.cursor()
        c.execute("DROP TABLE IF EXISTS pickup_servers")
        c.execute("CREATE TABLE IF NOT EXISTS pickup_servers( "
                  "row_id INTEGER PRIMARY KEY,"
                  "server_ip TEXT NOT NULL UNIQUE,"
                  "server_state TEXT,"
                  "process_stats_pid NUMERIC)")

        for server in self.servers:
            query_data = (server, 'FREE' )
            c.execute("INSERT INTO pickup_servers(server_ip, server_state) VALUES(?, ?)", query_data)
        self.db.commit()

    def init_pickups(self, config):
        if 'modes' in config:
            self.modes = config['modes']
            for mode in config['modes']:
                self.pickups[mode] = []
        else:
            print("Modes section does not exist in config file.")
            sys.exit(-1)

        if 'servers' in config:
            self.servers = config['servers']
            print("servers: \n "+ str(self.servers))
        else:
            print("Servers section does not exist in config file.")
            sys.exit(-1)

    def init_bot_config(self,config):
        if 'bot_account' in config and 'login' in config['bot_account'] and 'password' in config['bot_account']:
            self.account = config['bot_account']
        else:
            print("Bot account do not exist in config file.")
            sys.exit(-1)
        if 'bot_channel' in config:
            self.bot_channel = config['bot_channel']
        else:
            print("Bot channels do not exist in config file.")
            sys.exit(-1)

        if 'admin_role' in config:
            self.admin_role = config['admin_role']
        else:
            print("Admin role does not exise in config file.")
            sys.exit(-1)

    def on_read_evnt_handler(self):
        print("Bot ready {}".format(self.client.user.name))
        print("bot user id: " + self.client.user.id)

    @asyncio.coroutine
    def on_message_evnt_handler(self, msg):
        if msg.channel.id == self.bot_channel:
            if msg.content.lower().startswith("!add"):
                reply = self.add_player_to_list(msg)
                if isinstance(reply, tuple):
                    if len(reply) > 1:
                        yield from self.send_private_msgs(reply[1],"Your pickup '" + reply[2] + "' game is ready! Password to server: " + reply[3])
                    reply = reply[0]
                if reply is not None:
                    yield from self.client.send_message(msg.channel, reply)
            elif msg.content.lower().startswith("!remove"):
                reply = self.remove_player_from_list(msg)
                if reply is not None:
                    yield from self.client.send_message(msg.channel, reply)
            elif msg.content.lower().startswith("!who"):
                reply = self.get_status_of_all_pickups()
                if reply is not None:
                    yield from self.client.send_message(msg.channel, reply)

            elif msg.content.lower().startswith("!promote"):
                reply = self.promote_mode(msg)
                if reply is not None:
                    yield from self.client.send_message(msg.channel, reply)
            elif msg.content.lower().startswith("!help"):
                yield from  self.client.send_message(msg.channel, "Available commands:!add [mode] !remove [mode] !who !promote (every "+ str(self.promote_delay_minutes) + " minutes).")
            elif msg.content.lower().startswith("!servers") and self.author_has_admin_role(msg.author):
                yield from self.get_servers_info(msg)
        elif type(msg.author) is discord.User:  # private channel
            if msg.content.lower().startswith("!clear_sv_state"):# and author_has_admin_role(msg.author):
                self.clear_sv_state(msg)

    def get_status_of_pickup(self,mode):
        return "Current players for " + mode + ": " + self.users_obj_list_to_string_names(self.pickups[mode]) + ". ["+ str(len(self.pickups[mode])) + "/" + str(self.modes[mode]["maxplayers"])+"]"

    def get_status_of_all_pickups(self):
        status = ""
        for mode in self.modes:
            status += self.get_status_of_pickup(mode)
            status += "\n"
        return status


    def run_bot(self):
        self.client.run(self.account['login'], self.account['password'])

    def get_mode_from_msg(self, message):
        list_size = len(message.content.split())
        if list_size == 1: return "all"
        elif list_size > 1:
            mode = message.content.split()[1].lower()
            if mode in self.modes:
                return mode
        return None

    def add_player_to_list(self, message):
        mode = self.get_mode_from_msg(message)

        if mode == None or mode == "all":
            return ("Mode does not exist",);

        for player in self.pickups[mode]:
            if player.id == message.author.id:  # jesli juz jest na liscie
                return ("Already on " + mode + " list",)

        self.pickups[mode].append(message.author)

        if len(self.pickups[mode]) == self.modes[mode]["maxplayers"]:
            return self.start_game(mode)
        else:
            return ("Added for: " + mode + " ." + self.get_status_of_pickup(mode),)

    def remove_player_from_list(self, message):
        mode = self.get_mode_from_msg(message)
        if mode is None:
            return

        if mode == "all":
            info_msg = ""
            for mode in self.modes:
                for i, player in enumerate(self.pickups[mode]):
                    if player.id == message.author.id:
                        del (self.pickups[mode][i])
                        info_msg += "Removed from: " + mode + ". " + self.get_status_of_pickup(mode) + "\n"
            return info_msg

        else:
            for i, player in enumerate(self.pickups[mode]):
                if player.id == message.author.id:
                    del (self.pickups[mode][i])
                    return "Removed from: " + mode + ". " + self.get_status_of_pickup(mode)



    @asyncio.coroutine
    def send_private_msgs(self, userslist, text):  # info do każdego z listy
        for i, player in enumerate(userslist):
                try:
                    yield from self.client.send_message(player, text)
                except HTTPException:
                    yield from self.client.send_message(player, text)
                else:
                    break

    def mention_all_on_list(self, list):
        mentionStr = ""
        for i, player in enumerate(list):
            mentionStr += player.mention
        return mentionStr

    def users_obj_list_to_string_names(self, list):
        players = ""
        if not list:
            return "empty"
        for i, player in enumerate(list):
            players += player.name
            players += ", "
        return players[:-2]

    def get_captains_names(self, list):
        captains = random.sample(list, 2)
        return "" + captains[0].name + ", " + captains[1].name

    def get_server(self):
        for server in self.servers:
            temp = server.split(":")
            address = (temp[0], int(temp[1]))
            a2s = valve.source.a2s.ServerQuerier(address)
            try:
                info = a2s.get_info()
                rules = a2s.get_rules()
            except valve.source.a2s.NoResponseError:
                continue
            slots = info["max_players"] - info["player_count"]

            game_state = rules["rules"]["g_gameState"]

            pickup_state = self.get_pickup_server_state(server)

            print(info["server_name"] + server)
            if  game_state == 'PRE_GAME' and pickup_state == 'FREE':
                return (info["server_name"], server)
        return None

    def watch_server_until_game_ends(self, server_addres, stats_port, stats_password):
        print("proces utworzony")
        sv_addr_and_port = server_addres.split(":")

        self.set_pickup_server_state(server_addres, 'PICKUP_IN_PROGRESS', os.getpid())


        stats_watcher = StatsWatcher(server_addr=sv_addr_and_port[0], stats_port=stats_port, stats_password=stats_password)
        stats_watcher.connect_and_wait_for_end_of_game()

        self.set_pickup_server_state(server_addres,'FREE',None)

    def set_pickup_server_state(self, server, state, pid=None):
        c = self.db.cursor()

        query = "UPDATE pickup_servers SET server_state =?,process_stats_pid=?  WHERE server_ip = ?"
        query_data = (state, pid, server)

        c.execute(query, query_data)

        self.db.commit()

    @asyncio.coroutine
    def send_private_msgs(self, userslist, text):  # info do każdego z listy
        for i, player in enumerate(userslist):
            yield from self.client.send_message(player, text)


    def start_game(self, mode):
        info_msg = "" + self.mention_all_on_list(self.pickups[mode]) + "\nPickup starts. Signed players: **" + self.users_obj_list_to_string_names(
            self.pickups[mode]) + "**" + "\nCaptains: **" + self.get_captains_names(self.pickups[mode]) + "**"

        server = self.get_server()

        if server is None:
            self.pickups[mode].clear()
            info_msg += "\nError. Currently none of svs are available."
            return (info_msg,)
        else:
            addr_sv = server[1].split(":")

            p = Process(target=self.watch_server_until_game_ends, args=(server[1],self.servers[server[1]]["stats_port"],self.servers[server[1]]["stats_password"] ))
            p.start()

            playerslist = self.pickups[mode][:]
            info_msg += "\nSERVER NAME: " + server[0] + "\nSERVER IP: " + server[1] + "\nCONNECTION LINK: steam://connect/" + \
                      server[1];
            self.pickups[mode].clear()
            return (info_msg, playerslist, mode, "pickup")


    def promote_mode(self,msg):
        current_time = datetime.now()
        temp = self.get_mode_from_msg(msg)
        if temp is None:
            return "Mode does not exist."
        if self.last_promote_date_exec is not None :
            time_difference = current_time - self.last_promote_date_exec
            if time_difference < timedelta(minutes=self.promote_delay_minutes):
                time_to_wait_seconds = self.promote_delay_minutes*60 - time_difference.total_seconds()
                return "Luukie doesn't want spam here. You need to wait :" + str(int(time_to_wait_seconds/60)) + ":" + str(int(time_to_wait_seconds%60))

        self.last_promote_date_exec = current_time
        info_msg = "@everyone"
        if temp == "all":
            for mode in self.modes:
                info_msg += "\nNeed " + str(
                    self.modes[mode]["maxplayers"] - len(self.pickups[mode])) + " more players for " + mode + ". Type **!add " + mode + "** to add."
        else:
           info_msg += "\nNeed " + str(
                self.modes["modes"][temp]["maxplayers"] - len(self.pickups[temp])) + " more players for " + temp + ". Type **!add " + temp + "** to add."
        return info_msg;


    def get_servers_info(self, message):
        for server in self.servers:
            result = self.get_sv_info(server)

            replay = "IP: " + server

            if result != None:
                server_state = result[1]["rules"]["g_gameState"]
                pickup_state = self.get_pickup_server_state(server)
                if pickup_state == "PICKUP_IN_PROGRESS":
                    server_state = pickup_state
                replay += " online. Name: " + result[0]["server_name"] + " ; Players: " + str(
                    result[0]["player_count"]) + "/" + str(result[0]["max_players"]) + ". Gamestate: " + server_state
            else:
                replay += " offline."

            yield from self.client.send_message(message.channel, replay)

    def get_pickup_server_state(self,server):
        c = self.db.cursor()

        query = "SELECT server_state FROM pickup_servers WHERE server_ip=?"
        query_data = (server,)

        c.execute(query, query_data)
        checking_result = c.fetchone()
        if checking_result is not None:
            return checking_result[0]

    def get_sv_info(self, server):
        addr = server.split(":")
        address = (addr[0], int(addr[1]))
        a2s = valve.source.a2s.ServerQuerier(address)
        try:
            info = a2s.get_info()
            rules = a2s.get_rules()
        except valve.source.a2s.NoResponseError:
            return None

        return (info, rules)


    def author_has_admin_role(self, msg_author, role: str=None):
        if role is None:
            role = self.admin_role

        #member = find(lambda m: m.id == message.author.id, message.server.members)

        has_role = find(lambda r: r.name == role, msg_author.roles)

        return has_role is not None

    def clear_sv_state(self, message):
        server = message.content.replace('!clear_sv_state ', '', 1)
        c = self.db.cursor()

        query = "SELECT process_stats_pid FROM pickup_servers WHERE server_ip=?"
        query_data = (server,)
        c.execute(query, query_data)
        result = c.fetchone()
        if result != None and result[0] != None:
            os.kill(result[0], signal.SIGKILL)
            os.wait()
        self.set_pickup_server_state(server, "FREE")


    @asyncio.coroutine
    def on_member_update_evnt_handler(self, before, after):
        if after.status == discord.Status.offline:
            for mode, list in self.pickups.items():
                for i, player in enumerate(list):
                    if before.id == player.id:
                        del (self.pickups[mode][i])
                        yield from  self.client.send_message(self.client.get_channel(self.bot_channel),
                                                        before.name + " disconnected. Removed from: " + mode + " " + str(
                                                            len(self.pickups[mode])) + "/" + str(
                                                            self.modes[mode]["maxplayers"]))