# -*- coding: utf-8 -*-

import datetime
import socket
import sqlite3
import subprocess
import os
import signal
import sys
import traceback
from contextlib import contextmanager, closing

import click
import requests
import time

HOME = os.getenv('HOME', '/home/pi')
SCREENLY_DB_DIR = '.screenly/screenly.db'
SCREENLY_ASSETS_DIR = 'screenly_assets'
DIRECTORY_PATH = '/'.join(os.path.realpath(__file__).split('/')[:-1])

BASE_API_SCREENLY_URL = 'https://api.screenlyapp.com'

PORT_NGROK = 4040
PORT = None

ngrok_process = None
simplehttpserver_process = None

token = None
ngrok_public_url = None


################################
# Suprocesses
################################

def start_simplehttpserver_process(try_connection=100):
    global simplehttpserver_process
    click.echo(click.style("SimpleHTTPServer starting ...", fg='yellow'))
    simplehttpserver_process = subprocess.Popen('python -m SimpleHTTPServer %i' % PORT, stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT, shell=True, preexec_fn=os.setsid,
                                                cwd=os.path.join(HOME, SCREENLY_ASSETS_DIR))
    try_count = 0
    while True:
        if try_count >= try_connection:
            raise Exception('Failed start SimpleHTTPServer')
        try:
            requests.get('http://127.0.0.1:%i/' % PORT, timeout=10)
            break
        except requests.exceptions.ConnectionError:
            try_count += 1
            time.sleep(0.1)
            continue
    click.echo(click.style("SimpleHTTPServer successfull started", fg='green'))


def stop_simplehttpserver_process():
    global simplehttpserver_process
    if simplehttpserver_process:
        os.killpg(simplehttpserver_process.pid, signal.SIGKILL)
        click.echo(click.style('SimpleHTTPServer stopped', fg='green'))


def start_http_ngrok_process(try_connection=100):
    global ngrok_process
    click.echo(click.style("Ngrok starting ...", fg='yellow'))
    ngrok_process = subprocess.Popen('./ngrok http %i' % PORT, stderr=subprocess.STDOUT, stdout=subprocess.PIPE,
                                     shell=True, preexec_fn=os.setsid, cwd=DIRECTORY_PATH)
    try_count = 0
    while True:
        if try_count >= try_connection:
            raise Exception('Failed start ngrok')
        try:
            requests.get('http://127.0.0.1:%i' % PORT_NGROK, timeout=10)
            break
        except requests.exceptions.ConnectionError:
            try_count += 1
            time.sleep(0.1)
            continue
    click.echo(click.style("Ngrok successfull started", fg='green'))


def get_ngrock_public_url(try_connection=100):
    global ngrok_process
    try_count = 0
    while True:
        if try_count >= try_connection:
            raise Exception('Could not take a public url ngrok')
        response = requests.get('http://127.0.0.1:%i/api/tunnels' % PORT_NGROK, timeout=10).json()
        if response['tunnels']:
            break
        else:
            try_count += 1
            time.sleep(0.1)
            continue
    return response['tunnels'][0]['public_url']


def stop_ngrok_process():
    global ngrok_process
    if ngrok_process:
        os.killpg(ngrok_process.pid, signal.SIGKILL)
        click.echo(click.style("Ngrok stopped", fg='green'))


################################
# Utilities
################################
def set_free_port():
    global PORT
    for port in range(8000, 9999):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            res = sock.connect_ex(('localhost', port))
            if res != 0:
                PORT = port
                break


def progress_bar(count, total, text=''):
    """
    This simple console progress bar
    For display progress asset uploads
    """
    progress_line = "\xe2" * int(round(50 * count / float(total))) + '-' * (50 - int(round(50 * count / float(total))))
    percent = round(100.0 * count / float(total), 1)
    sys.stdout.write('[%s] %s%s %s\r' % (progress_line, percent, '%', text))
    sys.stdout.flush()


def set_token(value):
    global token
    token = 'Token %s' % value


def set_ngrok_public_url(value):
    global ngrok_public_url
    ngrok_public_url = value


################################
# Database
################################
@contextmanager
def cursor(connection):
    cur = connection.cursor()
    yield cur
    cur.close()


def is_active(asset, at_time=None):
    if asset['is_enabled'] and asset['start_date'] and asset['end_date']:
        at = at_time or datetime.datetime.utcnow()
        return 1 if asset['start_date'] < at < asset['end_date'] else 0
    return 0


def mkdict(keys):
    return lambda row: dict([(keys[ki], v) for ki, v in enumerate(row)])


def get_assets_from_db():
    assets = []
    keys = ["asset_id", "name", "uri", "start_date",
            "end_date", "duration", "mimetype", "is_enabled", "is_processing", "nocache", "play_order"]
    with sqlite3.connect(os.path.join(HOME, SCREENLY_DB_DIR), detect_types=sqlite3.PARSE_DECLTYPES) as conn:
        mk = mkdict(keys)

        with cursor(conn) as cur:
            cur.execute('select ' + ','.join(keys) + ' from assets order by play_order')
            assets = [mk(asset) for asset in cur.fetchall()]
        [asset.update({'is_active': is_active(asset)}) for asset in assets]

        return assets


################################
# Requests
################################

def send_asset(asset):
    endpoind_url = '%s/api/v3/assets/' % BASE_API_SCREENLY_URL
    headers = {
        'Authorization': token
    }
    asset_uri = asset['uri']
    if asset_uri.startswith(HOME):
        asset_uri = os.path.join(ngrok_public_url, asset['asset_id'])
    data = {
        'title': asset['name'],
        'source_url': asset_uri,
        # 'duration': asset['duration'] #CURRENTLY DOESN'T WORK
    }
    response = requests.post(endpoind_url, data=data, headers=headers)
    if response.status_code == 200:
        return True
    else:
        return False


def check_validate_token(api_key):
    endpoind_url = '%s/api/v3/assets/' % BASE_API_SCREENLY_URL
    headers = {
        'Authorization': 'Token %s' % api_key
    }
    response = requests.get(endpoind_url, headers=headers)
    if response.status_code == 200:
        return api_key
    else:
        return None


def get_api_key_by_credentials(username, password):
    endpoind_url = '%s/api/v3/tokens/' % BASE_API_SCREENLY_URL
    data = {
        'username': username,
        'password': password
    }
    response = requests.post(endpoind_url, data=data)
    if response.status_code == 200:
        return response.json()['token']
    else:
        return None


################################
################################

def start_migration():
    if click.confirm('Do you want to start assets migration?'):
        click.echo('\n')
        set_free_port()
        start_simplehttpserver_process()
        start_http_ngrok_process()
        set_ngrok_public_url(get_ngrock_public_url())
        assets_migration()


def assets_migration():
    assets = get_assets_from_db()
    assets_length = len(assets)
    click.echo('\n')
    for index, asset in enumerate(assets):
        asset_name = str(asset['name'])
        progress_bar(index + 1, assets_length, text='Asset in migration progress: %s' % asset_name)
        status = send_asset(asset)
    click.echo('\n')
    click.echo(click.style('Migration completed successfully', fg='green'))


@click.group(invoke_without_command=True)
@click.option('--method',
              prompt='What do you want to use for migration?\n1.API token\n2.Credentials\n0.Exit\nYour choice',
              type=click.Choice(['1', '2', '0']))
def main(method):
    try:
        if method == '1':
            migrate_with_api_key()
        elif method == '2':
            migrate_with_credentials()
        elif method == '0':
            sys.exit(0)
    except Exception:
        traceback.print_exc()
    finally:
        click.echo('\n')
        stop_ngrok_process()
        stop_simplehttpserver_process()


@main.command()
@click.option('--api_key', prompt='Your API key')
def migrate_with_api_key(api_key):
    valid_token = check_validate_token(api_key)
    if valid_token:
        set_token(valid_token)
        click.echo(click.style('Successfull authentication', fg='green'))
        start_migration()
    else:
        click.echo(click.style('Failed authentication', fg='red'))


@click.command()
@click.option('--username', prompt='Your username')
@click.option('--password', prompt='Your password', hide_input=True)
def migrate_with_credentials(username, password):
    valid_token = get_api_key_by_credentials(username, password)
    if valid_token:
        set_token(valid_token)
        click.echo(click.style('Successfull authentication', fg='green'))
        start_migration()
    else:
        click.echo(click.style('Failed authentication', fg='red'))


if __name__ == '__main__':
    click.echo(click.style("""
       _____                           __         ____  _____ ______
      / ___/_____________  ___  ____  / /_  __   / __ \/ ___// ____/
      \__ \/ ___/ ___/ _ \/ _ \/ __ \/ / / / /  / / / /\__ \/ __/
     ___/ / /__/ /  /  __/  __/ / / / / /_/ /  / /_/ /___/ / /___
    /____/\___/_/   \___/\___/_/ /_/_/\__, /   \____//____/_____/
                                     /____/
    """, fg='blue'))

    main()
