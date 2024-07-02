import sys
import os
import re
import csv
import json
import asyncio
import time
from datetime import datetime, timezone
from telethon import TelegramClient, events, types
from colorama import init, Fore, Style
from urllib.request import Request, urlopen
import winsound

# Initialize colorama
init()

def read_config(file_path):
    config = {}
    sources = []
    in_sources_section = False
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line == '[sources]':
                in_sources_section = True
                continue
            if in_sources_section:
                if line.startswith('['):
                    in_sources_section = False
                elif line:
                    sources.append(line)
            elif '=' in line:
                key, value = line.split('=', 1)
                config[key] = value
    config['sources'] = sources
    return config

script_dir = os.path.dirname(os.path.abspath(__file__))
config_file_path = os.path.join(script_dir, 'config.txt')
config = read_config(config_file_path)

api_id = int(config['api_id'])
api_hash = config['api_hash']
send = config.get('send', config.get('destination'))
sources = config['sources']

client = TelegramClient('personal', api_id, api_hash)

start_time = datetime.now().astimezone()

sent_cas = set()

ca_file_path = os.path.join(script_dir, "blacklist.txt")

blacklist = set()
try:
    with open(ca_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            blacklist.add(line.strip())
    print(Fore.YELLOW + f"Blacklist loaded...", Fore.WHITE + f"({datetime.now().strftime('%H:%M:%S %Y-%m-%d')})")
except FileNotFoundError:
    print(f"Blacklist file not found, starting with an empty blacklist...")

csv_file_path = os.path.join(script_dir, "transactions_log.csv")
csv_header = ['Sender', 'CA', 'Timestamp']

if not os.path.exists(csv_file_path):
    with open(csv_file_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(csv_header)

print(Fore.GREEN + f"Waiting for message...", Fore.WHITE)

async def fetch_dexscreener_data(ca):
    url = f'https://api.dexscreener.io/latest/dex/search?q={ca}'
    headers = {'User-Agent': 'Mozilla/5.0'}
    req = Request(url=url, headers=headers)
    try:
        with urlopen(req) as response:
            if response.status == 200:
                data = json.loads(response.read().decode('utf-8'))
                if 'pairs' in data and data['pairs']:
                    pairAddress = data["pairs"][0]["pairAddress"]
                    tokenAddress = data["pairs"][0]["baseToken"]['address']
                    return pairAddress, tokenAddress
    except Exception as e:
        print(f"Error fetching data from dexscreener: {e}")
    return None, None

async def retry_request(func, *args, retries=5, backoff_factor=0.5):
    for attempt in range(retries):
        try:
            return await func(*args)
        except Exception as e:
            print(Fore.RED + f"Attempt {attempt + 1} failed: {e}", Fore.WHITE)
            if attempt < retries - 1:
                sleep_time = backoff_factor * (2 ** attempt)
                print(Fore.YELLOW + f"Retrying in {sleep_time} seconds...", Fore.WHITE)
                time.sleep(sleep_time)
    print(Fore.RED + "All retry attempts failed.", Fore.WHITE)
    return None

@client.on(events.NewMessage)
async def event_handler(event):
    if event.date.replace(tzinfo=timezone.utc) < start_time:
        return

    message = event.raw_text
    sender_username = None
    sender_id = None
    try:
        chat = await event.get_chat()
        if isinstance(chat, types.User):
            sender_username = chat.username
        elif isinstance(chat, (types.Chat, types.Channel)):
            sender_username = chat.username
            sender_id = str(chat.id)
    except Exception as e:
        print(f"Error getting chat info: {e}")

    if sender_username not in sources and sender_id not in sources:
        return

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] Received message from: {sender_username or sender_id}")

    async def process_ca(ca):
        if ca in blacklist:
            print(f"[{timestamp}] CA {ca} Blacklisted...", Fore.RED + "Ignoring...", Fore.WHITE)
            print(Fore.GREEN + f"Waiting on transaction...", Fore.WHITE)
            return
        if ca not in sent_cas:
            sent_cas.add(ca)
            sender = chat.title if hasattr(chat, 'title') else sender_username
            print(Fore.GREEN + f"CA: {ca}... ({timestamp}) Buy", Fore.WHITE)
            await client.send_message(send, ca)
            winsound.Beep(1500, 800)

            if ca not in blacklist:
                with open(ca_file_path, 'a', encoding='utf-8') as f:
                    f.write(ca + '\n')
                blacklist.add(ca)

            pair_address, token_address = await retry_request(fetch_dexscreener_data, ca)

            addresses_to_add = set()
            if pair_address:
                addresses_to_add.add(pair_address)
            if token_address:
                addresses_to_add.add(token_address)

            for address in addresses_to_add:
                if address not in blacklist:
                    blacklist.add(address)
                    with open(ca_file_path, 'a', encoding='utf-8') as f:
                        f.write(address + '\n')

            with open(csv_file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([sender, ca, timestamp])

            print(Fore.GREEN + f"Waiting on transaction...", Fore.WHITE)
        else:
            print(f"[{timestamp}] CA already sent: {ca}...", Fore.RED + "Not Buying", Fore.WHITE)
            print(Fore.GREEN + f"Waiting on transaction...", Fore.WHITE)

    birdeye_match = re.search(r'https://birdeye\.so/token/([^?]+)\?', message)
    if (birdeye_match):
        ca = birdeye_match.group(1)
        await process_ca(ca)
        return

    dexscreener_match = re.search(r'https://dexscreener\.com/solana/([a-zA-Z0-9]+)', message)
    if (dexscreener_match):
        ca = dexscreener_match.group(1)
        await process_ca(ca)
        return

    ca_line_match = re.search(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b', message)
    if (ca_line_match):
        ca = ca_line_match.group(0)
        await process_ca(ca)
    else:
        print(Fore.RED + f"No CA found in the message...", Fore.WHITE)
        print(Fore.GREEN + f"Waiting on transaction...", Fore.WHITE)

async def main():
    await client.start()
    print("Client started. Fetching destination bot entity...")
    await client.get_dialogs()
    print(Fore.BLUE + f"Loaded Configuration:\nAPI ID: {api_id}\nAPI Hash: {api_hash}\nDestination: {send}\nSources: {sources}", Fore.WHITE)
    print(Fore.GREEN + f"Listening for new messages...", Fore.WHITE)
    await client.run_until_disconnected()

asyncio.run(main())
