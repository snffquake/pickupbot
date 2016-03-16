import argparse
from pickupbot import PickupBot
from os import path


def main():
    parser = argparse.ArgumentParser(description='Pickup discord bot',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-c', '--config',
                        dest='config',
                        default='config.json',
                        help='Config file location')

    args = parser.parse_args()

    config_file = path.expanduser(args.config)

    pickup = PickupBot(args.config)

    pickup.run_bot()


if __name__ == '__main__':
    main()