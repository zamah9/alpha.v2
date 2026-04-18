import os
import json
import subprocess
import asyncio
import time
import random
import string
import base64
import uuid
from telethon import TelegramClient, events, Button
from telethon.errors import AlreadyInConversationError

# ==========================================
# KONFIGURASI UTAMA
# ==========================================
DIR_MAIN = '/usr/local/sbin/alpha-vps'
CONFIG_FILE = f'{DIR_MAIN}/config.json'
DB_USERS = f'{DIR_MAIN}/users_db.json'
PRICES = { "ssh": 5000, "vmess": 5000, "vless": 5000 } # Harga per akun
DEFAULT_EXP = 30 # Masa aktif default (hari) untuk pembelian
WS_PATH = "/ssh" 

# ==========================================
# LOAD CONFIG & DATABASE
# ==========================================
if not os.path.exists(DIR_MAIN):
    os.makedirs(DIR_MAIN)

# Cek Config
try:
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
    # Inisialisasi Telethon (Gantikan Telebot)
    bot = TelegramClient('store_session', config['api_id'], config['api_hash']).start(bot_token=config['bot_token'])
    ADMIN_ID = int(config['admin_id'])
    DOMAIN = config.get('domain', "127.0.0.1")
except Exception as e:
    print(f"Error Config: {e}")
    print("Pastikan config.json berisi: api_id, api_hash, bot_token, admin_id")
    exit()

# Cek Database Saldo
if not os.path.exists(DB_USERS):
    with open(DB_USERS, 'w') as f: json.dump({}, f)

def load_db():
    try:
        with open(DB_USERS, 'r') as f: return json.load(f)
    except: return {}

def save_db(data):
    with open(DB_USERS, 'w') as f: json.dump(data, f, indent=4)

def get_balance(uid):
    db = load_db()
    return db.get(str(uid), {}).get('balance', 0)

def reduce_balance(uid, amount):
    db = load_db()
    uid = str(uid)
    if uid not in db: db[uid] = {'balance': 0}
    
    if db[uid]['balance'] < amount:
        return False
    
    db[uid]['balance'] -= amount
    save_db(db)
    return True

def add_balance(uid, amount):
    db = load_db()
    uid = str(uid)
    if uid not in db: db[uid] = {'balance': 0}
    db[uid]['balance'] += amount
    save_db(db)

# ==========================================
# FUNGSI CREATE AKUN (BACKEND)
# ==========================================
def get_rand_pass():
    return ''.join(random.choice(string.ascii_letters + string.digits) for i in range(6))

def create_ssh_system(user, pw, days):
    try:
        # Menghitung tanggal expired
        cmd_date = f"date -d '+{days} days' +'%Y-%m-%d'"
        exp = subprocess.check_output(cmd_date, shell=True).decode().strip()
        
        # Membuat user system
        cmd_add = f"useradd -e {exp} -s /bin/false -M {user} && echo '{user}:{pw}' | chpasswd"
        subprocess.check_output(cmd_add, shell=True)
        return True, exp
    except Exception as e:
        return False, str(e)

def create_xray_system(proto, user, days):
    try:
        uid = str(uuid.uuid4())
        path = '/etc/xray/config.json'
        
        if not os.path.exists(path):
            return False, "Xray Config Not Found"

        with open(path, 'r') as f: data = json.load(f)
        
        found = False
        for ib in data['inbounds']:
            if ib['protocol'] == proto:
                client = {"id": uid, "email": f"{user}@{proto}"}
                if proto == "vmess": client["alterId"] = 0
                # Tambahkan client ke list
                if 'clients' in ib['settings']:
                    ib['settings']['clients'].append(client)
                else:
                    ib['settings']['clients'] = [client]
                found = True
                break
        
        if found:
            with open(path, 'w') as f: json.dump(data, f, indent=2)
            os.system('systemctl restart xray')
            return True, uid
        return False, "Protocol Not Found in Config"
    except Exception as e:
        return False, str(e)

# ==========================================
# BOT INTERFACE (MENU & LOGIC)
# ==========================================

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    sender = await event.get_sender()
    uid = sender.id
    bal = get_balance(uid)
    
    buttons = [
        [Button.inline(f"🚀 Buy SSH (Rp {PRICES['ssh']:,})", data="buy_ssh")],
        [Button.inline(f"⚡ Buy VMESS (Rp {PRICES['vmess']:,})", data="buy_vmess"),
         Button.inline(f"🌐 Buy VLESS (Rp {PRICES['vless']:,})", data="buy_vless")],
        [Button.inline("💰 Cek Saldo", data="cek_saldo")]
    ]
    
    # Menu Khusus Admin
    if uid == ADMIN_ID:
        buttons.append([Button.inline("➕ Topup Saldo (Admin)", data="admin_topup")])

    msg = f"""
<b>🛍️ ALPHA STORE PANEL</b>
━━━━━━━━━━━━━━━━━━━
👋 <b>Halo,</b> {sender.first_name}
🆔 <b>ID:</b> <code>{uid}</code>
💰 <b>Saldo:</b> <code>Rp {bal:,}</code>
━━━━━━━━━━━━━━━━━━━
Silakan pilih layanan di bawah ini:
"""
    await event.respond(msg, buttons=buttons, parse_mode='html')

# HANDLER CEK SALDO
@bot.on(events.CallbackQuery(data=b'cek_saldo'))
async def cek_saldo(event):
    uid = event.sender_id
    bal = get_balance(uid)
    await event.answer(f"💰 Saldo Anda: Rp {bal:,}", alert=True)

# HANDLER PEMBELIAN (INTERAKTIF)
@bot.on(events.CallbackQuery(pattern=b'buy_(.*)'))
async def buy_handler(event):
    tipe = event.data.decode().split('_')[1] # ssh, vmess, atau vless
    uid = event.sender_id
    chat = event.chat_id
    harga = PRICES.get(tipe, 0)
    bal = get_balance(uid)

    # 1. Cek Saldo Awal
    if bal < harga:
        await event.answer(f"❌ Saldo Kurang! Butuh Rp {harga:,}", alert=True)
        return

    # Mulai Percakapan Interaktif
    try:
        async with bot.conversation(chat, timeout=120) as convo:
            # A. Input Username
            await convo.send_message(f"<b>🛒 PEMBELIAN {tipe.upper()}</b>\n\nMasukkan <b>Username</b> yang diinginkan:\n(Ketik /cancel untuk batal)", parse_mode='html')
            username_msg = await convo.get_response()
            username = username_msg.text.strip()
            
            if username == '/cancel':
                await convo.send_message("❌ Transaksi Dibatalkan.")
                return
            
            # B. Input Password (Khusus SSH)
            password = get_rand_pass() # Default random
            if tipe == 'ssh':
                await convo.send_message(f"Masukkan <b>Password</b>:\n(Ketik 'auto' untuk password acak)", parse_mode='html')
                pw_msg = await convo.get_response()
                if pw_msg.text.strip().lower() != 'auto':
                    password = pw_msg.text.strip()
            
            # C. Konfirmasi
            confirm_msg = await convo.send_message(
                f"<b>KONFIRMASI PEMBELIAN</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Layanan: {tipe.upper()}\n"
                f"👤 User: {username}\n"
                f"💰 Harga: Rp {harga:,}\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"Ketik <b>SETUJU</b> untuk memproses.", parse_mode='html'
            )
            confirm_input = await convo.get_response()
            
            if confirm_input.text.strip().lower() != 'setuju':
                await convo.send_message("❌ Transaksi Dibatalkan.")
                return

            # PROSES TRANSAKSI
            wait_msg = await convo.send_message("⏳ Sedang memproses...")
            
            # Potong Saldo (Cek lagi takutnya double)
            if not reduce_balance(uid, harga):
                await wait_msg.edit("❌ Saldo tidak cukup saat memproses.")
                return

            # Create Akun
            success = False
            result_text = ""
            
            if tipe == 'ssh':
                ok, res = create_ssh_system(username, password, DEFAULT_EXP)
                if ok:
                    success = True
                    result_text = f"""
✅ <b>SSH CREATED SUCCESSFULLY</b>
━━━━━━━━━━━━━━━━━━━
<b>Username:</b> <code>{username}</code>
<b>Password:</b> <code>{password}</code>
<b>Expired:</b> {res}
<b>Host:</b> <code>{DOMAIN}</code>
━━━━━━━━━━━━━━━━━━━
Payload WS:
<code>GET {WS_PATH} HTTP/1.1[crlf]Host: {DOMAIN}[crlf]Upgrade: websocket[crlf][crlf]</code>
"""
                else:
                    result_text = f"❌ Gagal membuat SSH: {res}"

            elif tipe in ['vmess', 'vless']:
                ok, res = create_xray_system(tipe, username, DEFAULT_EXP) # res adalah UUID atau Error
                if ok:
                    success = True
                    link = ""
                    if tipe == 'vmess':
                        cfg = {"v":"2","ps":username,"add":DOMAIN,"port":"443","id":res,"net":"ws","path":f"/{tipe}","tls":"tls"}
                        link = "vmess://" + base64.b64encode(json.dumps(cfg).encode()).decode()
                    else:
                        link = f"vless://{res}@{DOMAIN}:443?path=%2F{tipe}&security=tls&type=ws#{username}"
                    
                    result_text = f"""
✅ <b>{tipe.upper()} CREATED SUCCESSFULLY</b>
━━━━━━━━━━━━━━━━━━━
<b>Remarks:</b> <code>{username}</code>
<b>Domain:</b> <code>{DOMAIN}</code>
<b>UUID:</b> <code>{res}</code>
<b>Expired:</b> {DEFAULT_EXP} Days
━━━━━━━━━━━━━━━━━━━
<b>Link:</b> <code>{link}</code>
"""
                else:
                    result_text = f"❌ Gagal membuat {tipe.upper()}: {res}"

            await wait_msg.delete()
            
            if success:
                await convo.send_message(result_text, parse_mode='html')
            else:
                # Refund saldo jika gagal
                add_balance(uid, harga)
                await convo.send_message(f"⚠️ Transaksi Gagal. Saldo dikembalikan.\nError: {result_text}")

    except AlreadyInConversationError:
        await event.respond("⚠️ Selesaikan transaksi sebelumnya dulu!", alert=True)
    except asyncio.TimeoutError:
        await event.respond("❌ Waktu habis. Transaksi dibatalkan.")
    except Exception as e:
        await event.respond(f"❌ Error System: {e}")

# HANDLER TOPUP (ADMIN)
@bot.on(events.CallbackQuery(data=b'admin_topup'))
async def topup_handler(event):
    chat = event.chat_id
    if event.sender_id != ADMIN_ID: return

    try:
        async with bot.conversation(chat) as convo:
            await convo.send_message("<b>ADMIN TOPUP</b>\nMasukkan format: <code>ID_USER JUMLAH</code>\nContoh: <code>12345678 50000</code>", parse_mode='html')
            resp = await convo.get_response()
            try:
                target_id, amount = resp.text.split()
                add_balance(target_id, int(amount))
                await convo.send_message(f"✅ Sukses tambah Rp {amount} ke ID {target_id}")
            except:
                await convo.send_message("❌ Format salah.")
    except: pass

print("Store Bot Berjalan (Telethon)...")
bot.run_until_disconnected()
