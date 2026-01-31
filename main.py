import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from dotenv import load_dotenv
import asyncio
import random
from datetime import datetime, timedelta, timezone, time
import aiohttp
import logging
import sys

# Impor kelas DatabaseManager yang kita buat
from database import DatabaseManager
from keep_alive import keep_alive

# --- Konfigurasi & Variabel Global ---
load_dotenv(override=True)

# Setup Logging agar error muncul di Leapcell
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

TOKEN = os.getenv('DISCORD_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
try:
    BIRTHDAY_CHANNEL_ID = int(os.getenv('BIRTHDAY_CHANNEL_ID', '0')) # Ambil dari .env
except ValueError:
    BIRTHDAY_CHANNEL_ID = 0

# Hadiah Koin
BIRTHDAY_REWARD = 1000
TEBAK_KATA_REWARD = 150
MATH_BATTLE_REWARD = 100
HIGHER_LOWER_REWARD = 300

# Konfigurasi Leveling
XP_PER_MESSAGE_MIN = 15
XP_PER_MESSAGE_MAX = 25
XP_COOLDOWN_SECONDS = 60

KATA_LIST = [
    # Kata-kata sehari-hari
    "rumah", "sekolah", "komputer", "internet", "jendela", "pintu", "kursi", "meja", 
    "buku", "pensil", "sepeda", "motor", "mobil", "hujan", "matahari", "bulan", 
    "bintang", "keluarga", "teman", "makanan", "minuman", "bermain", "belajar", 
    "bekerja", "tidur", "terbang", "berenang", "berjalan", "berlari", "tertawa", 
    "menangis", "bahagia", "sedih", "marah", "takut", "kucing", "anjing", "burung", 
    "ikan", "pohon", "bunga", "gunung", "pantai", "laut", "sungai", "danau", 
    "jembatan", "telepon", "televisi", "radio", "musik", "film", "olahraga", 
    "sepakbola", "basket", "badminton", "indonesia", "jakarta", "bandung", 
    "surabaya", "medan", "makassar", "kemerdekaan", "pendidikan", "kesehatan",
    "transportasi", "komunikasi", "teknologi", "lingkungan", "pemerintah"
]

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True # Diperlukan untuk leaderboard server
        
        super().__init__(command_prefix='!', intents=intents)
        
        # Inisialisasi DatabaseManager
        self.db = DatabaseManager(dsn=DATABASE_URL)
        # Cooldown untuk on_message agar tidak membebani DB
        self.xp_cooldowns = {}

    async def login(self, token: str) -> None:
        # FIX: Bypass SSL verification dipindahkan ke sini agar dijalankan di dalam event loop
        print("🔧 Mengatur koneksi SSL bypass...", flush=True)
        self.http.connector = aiohttp.TCPConnector(ssl=False)
        await super().login(token)

    async def setup_hook(self):
        # Hubungkan ke DB dan siapkan tabel sebelum bot siap
        print("⚙️  Sedang menghubungkan ke Database...", flush=True)
        try:
            await self.db.connect()
            await self.db.init_db()
            print("✅ Database terhubung dan tabel siap.", flush=True)
        except Exception as e:
            logging.error(f"❌ Gagal inisialisasi database: {e}")
            raise e

        # Mulai background task
        self.birthday_checker.start()
        
        # Selama development, lebih baik sync per server menggunakan !sync.
        # Baris di bawah ini bisa diaktifkan kembali jika bot sudah final.
        # await self.tree.sync()
    
    async def on_ready(self):
        print(f'✅ BOT ONLINE: {self.user} (ID: {self.user.id}) siap digunakan!', flush=True)
        print('------', flush=True)
    
    async def close(self):
        await self.db.close()
        await super().close()

    # Definisikan background task yang berjalan setiap hari pada waktu tertentu
    @tasks.loop(time=time(hour=0, minute=1, tzinfo=timezone.utc)) # Berjalan setiap hari jam 00:01 UTC
    async def birthday_checker(self):
        await self.wait_until_ready() # Tunggu hingga bot siap dan cache terisi

        if BIRTHDAY_CHANNEL_ID == 0:
            print("BIRTHDAY_CHANNEL_ID tidak diatur, task ulang tahun dilewati.")
            return

        channel = self.get_channel(BIRTHDAY_CHANNEL_ID)
        if not channel:
            print(f"Channel dengan ID {BIRTHDAY_CHANNEL_ID} tidak ditemukan.")
            return

        today_str = datetime.now(timezone.utc).strftime('%m-%d')
        birthdays_today = await self.db.get_birthdays_today(today_str)

        if not birthdays_today:
            return # Tidak ada yang ulang tahun, keluar diam-diam

        print(f"Menemukan {len(birthdays_today)} orang yang ulang tahun hari ini!")
        for record in birthdays_today:
            user_id = record['user_id']
            user = self.get_user(user_id)

            if user:
                # Beri hadiah koin
                user_data = await self.db.get_user_data(user_id)
                new_balance = user_data['coins'] + BIRTHDAY_REWARD
                await self.db.update_user_balance(user_id, new_balance)

                # Kirim ucapan
                embed = discord.Embed(title="🎉 Selamat Ulang Tahun! 🎂", description=f"Semoga panjang umur dan sehat selalu, {user.mention}! Sebagai hadiah, kamu mendapatkan **{BIRTHDAY_REWARD}** koin!", color=discord.Color.magenta())
                embed.set_thumbnail(url=user.display_avatar.url)
                await channel.send(embed=embed)
    
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        ctx = await self.get_context(message)

        await self.process_commands(message)

        if ctx.valid:
            return

        # --- Logika Pemberian XP (hanya untuk pesan biasa, bukan command) ---
        user_id = message.author.id
        now = datetime.now(timezone.utc)

        # Cek cooldown dari cache lokal dulu untuk efisiensi
        last_msg_time = self.xp_cooldowns.get(user_id)
        if last_msg_time and (now - last_msg_time).total_seconds() < XP_COOLDOWN_SECONDS:
            return
        
        self.xp_cooldowns[user_id] = now

        # Berikan XP
        user_data = await self.db.get_user_data(user_id)
        xp_to_add = random.randint(XP_PER_MESSAGE_MIN, XP_PER_MESSAGE_MAX)
        await self.db.grant_xp(user_id, xp_to_add)
        
        # Cek untuk level up
        current_level = user_data.get('level', 1)
        current_xp = user_data.get('xp', 0) + xp_to_add
        xp_needed = (current_level * 100)

        if current_xp >= xp_needed:
            new_level = current_level + 1
            xp_left_over = current_xp - xp_needed
            await self.db.update_level(user_id, new_level, xp_left_over)
            
            # UI Level Up Baru
            embed = discord.Embed(
                title="🎉 LEVEL UP!",
                description=f"Selamat {message.author.mention}, kamu telah naik ke **Level {new_level}**!",
                color=discord.Color.gold()
            )
            embed.add_field(name="📈 Level", value=f"{current_level} ➔ **{new_level}**", inline=True)
            embed.add_field(name="✨ XP", value=f"{xp_left_over} XP (Next: {new_level * 100})", inline=True)
            embed.set_thumbnail(url=message.author.display_avatar.url)
            embed.set_footer(text="Terus aktif untuk mencapai level berikutnya!")
            await message.channel.send(embed=embed)

bot = MyBot()

# --- Helper Function untuk Auto-Delete Pesan ---
async def send_auto_delete(interaction: discord.Interaction, content: str = None, embed: discord.Embed = None, delay: int = 3, ephemeral: bool = True):
    """Mengirim pesan yang akan menghapus dirinya sendiri setelah delay tertentu."""
    if interaction.response.is_done():
        msg = await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
        msg = await interaction.original_response()
    
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except (discord.NotFound, discord.HTTPException):
        pass

# Global error handler untuk slash commands
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.CommandOnCooldown):
        embed = discord.Embed(title="⏳ Cooldown", description=f"Perintah ini sedang dalam cooldown. Coba lagi dalam **{error.retry_after:.2f} detik**.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    elif isinstance(error, app_commands.errors.MissingPermissions):
        embed = discord.Embed(title="❌ Akses Ditolak", description="Anda tidak memiliki izin yang diperlukan untuk menjalankan perintah ini.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    elif isinstance(error, app_commands.errors.CheckFailure):
        # Ini akan menangkap kegagalan dari is_owner() dan cek lainnya
        embed = discord.Embed(title="❌ Akses Ditolak", description="Anda tidak memenuhi syarat untuk menggunakan perintah ini.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        # Untuk error lain, catat di konsol dan beri tahu pengguna.
        print(f"Error tak tertangani untuk command /{interaction.command.name}: {error}")
        # Pastikan untuk merespons interaksi agar tidak gagal
        embed = discord.Embed(title="❌ Error", description="Terjadi kesalahan saat menjalankan perintah.", color=discord.Color.red())
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

# --- Command Tambahan untuk Developer ---
# Ketik '!sync' di chat untuk memunculkan command baru secara instan
@bot.command()
@commands.is_owner()
async def sync(ctx):
    # Strategi ini (clear -> copy -> sync) memastikan command tidak duplikat dan selalu fresh.
    # Ini membuat `!sync` bisa dijalankan berkali-kali tanpa masalah.
    
    # 1. Hapus daftar command lama untuk server ini dari memori bot.
    bot.tree.clear_commands(guild=ctx.guild)
    
    # 2. Salin semua command global yang ada di kode ke dalam daftar command untuk server ini.
    bot.tree.copy_global_to(guild=ctx.guild)
    
    # 3. Kirim daftar command yang baru ke Discord.
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f"✅ Berhasil sinkronisasi {len(synced)} command ke server ini! Coba ketik / sekarang.")

@bot.command()
@commands.is_owner()
async def clearglobal(ctx):
    # Menghapus command global via API langsung agar tidak menghapus command di memori bot
    # Ini mencegah !sync menjadi 0 setelah menjalankan command ini
    await bot.http.bulk_upsert_global_commands(bot.application_id, [])
    await ctx.send("✅ Berhasil menghapus semua command Global. Sekarang hanya command Server (yang di-sync via `!sync`) yang akan muncul. Masalah double command seharusnya sudah teratasi.")

@bot.command()
@commands.is_owner()
async def unsync(ctx):
    # Menghapus semua command khusus dari server ini
    bot.tree.clear_commands(guild=ctx.guild)
    await bot.tree.sync(guild=ctx.guild)
    await ctx.send("✅ Berhasil menghapus semua command dari server ini. Gunakan `!sync` untuk menambahkannya kembali.")

@bot.tree.command(name="ping", description="Mengecek latensi bot")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000) 
    color = discord.Color.green() if latency < 100 else discord.Color.orange() if latency < 200 else discord.Color.red()
    embed = discord.Embed(title="🏓 Pong!", description=f"Latensi bot saat ini:", color=color)
    embed.add_field(name="Latency", value=f"**{latency}ms**", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Tampilkan daftar semua perintah yang tersedia.")
async def help_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title="Bantuan Perintah Bot",
        description="Berikut adalah daftar perintah slash (/) yang bisa kamu gunakan:",
        color=discord.Color.blurple()
    )

    # Kategori untuk pengelompokan
    category_map = {
        "General": "⚙️ Perintah Umum",
        "Game": "🎲 Game",
        "Judi": "🎰 Game Judi",
        "Birthday": "🎂 Ulang Tahun",
        "Sosial": "👥 Sosial & Ekonomi",
        "Fun": "🎭 Hiburan & Interaksi"
    }
    
    commands_by_category = {v: [] for v in category_map.values()}
    
    all_commands = bot.tree.get_commands()

    # Kategorikan semua perintah
    for cmd in sorted(all_commands, key=lambda c: c.name):
        # Handle groups
        if isinstance(cmd, app_commands.Group):
            if cmd.name == "game":
                for sub_cmd in sorted(cmd.commands, key=lambda c: c.name):
                    # Kategorikan game judi dan non-judi
                    if sub_cmd.name in ["risktower", "energycore", "shadowdeal", "guessnumber", "slotmachine", "blackjack", "balapan", "coinflip", "uno"]:
                        commands_by_category[category_map["Judi"]].append(f"`/{cmd.name} {sub_cmd.name}`: {sub_cmd.description}")
            elif cmd.name == "birthday":
                for sub_cmd in sorted(cmd.commands, key=lambda c: c.name):
                    commands_by_category[category_map["Birthday"]].append(f"`/{cmd.name} {sub_cmd.name}`: {sub_cmd.description}")
            elif cmd.name == "fun":
                for sub_cmd in sorted(cmd.commands, key=lambda c: c.name):
                    commands_by_category[category_map["Fun"]].append(f"`/{cmd.name} {sub_cmd.name}`: {sub_cmd.description}")
        # Handle standalone commands
        else:
            if cmd.name in ["tebakkata", "mathbattle", "higherlower"]:
                commands_by_category[category_map["Game"]].append(f"`/{cmd.name}`: {cmd.description}")
            elif cmd.name == "rps":
                commands_by_category[category_map["Judi"]].append(f"`/{cmd.name}`: {cmd.description}")
            elif cmd.name in ["cekkantong", "daily", "profile", "rep", "leaderboard", "pay"]:
                commands_by_category[category_map["Sosial"]].append(f"`/{cmd.name}`: {cmd.description}")
            elif cmd.name not in ["help"]: # Jangan tampilkan command help di dalam help
                commands_by_category[category_map["General"]].append(f"`/{cmd.name}`: {cmd.description}")

    # Tambahkan perintah admin secara terpisah jika pengguna adalah pemilik bot
    if await bot.is_owner(interaction.user):
        commands_by_category["👑 Perintah Admin"] = []
        admin_group = discord.utils.get(all_commands, name="admin")
        if admin_group and isinstance(admin_group, app_commands.Group):
            for sub_cmd in sorted(admin_group.commands, key=lambda c: c.name):
                commands_by_category["👑 Perintah Admin"].append(f"`/admin {sub_cmd.name}`: {sub_cmd.description}")

    # Bangun field embed dari kategori yang sudah diisi
    for category, command_list in commands_by_category.items():
        if command_list:
            embed.add_field(name=f"**{category}**", value="\n".join(command_list), inline=False)

    embed.set_footer(text="Gunakan perintah dengan mengetik '/' diikuti nama perintah.")
    await interaction.followup.send(embed=embed)

# Command untuk mengecek saldo
@bot.tree.command(name="cekkantong", description="Cek isi kantong koin Anda.")
async def cekkantong(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    # Ambil data dari database
    user_data = await bot.db.get_user_data(user_id)
    coins = user_data['coins']

    # Tentukan status kekayaan dan warna embed
    if coins < 100:
        status = "Butuh Donasi 🥺"
        color = discord.Color.light_grey()
        desc = "Dompetmu kering kerontang..."
    elif coins < 1000:
        status = "Warga Biasa 😐"
        color = discord.Color.blue()
        desc = "Cukup buat jajan cilok."
    elif coins < 5000:
        status = "Menengah ke Atas 💼"
        color = discord.Color.green()
        desc = "Lumayan, bisa buat traktir teman."
    elif coins < 20000:
        status = "Sultan Lokal 👑"
        color = discord.Color.gold()
        desc = "Uang bukan masalah bagimu."
    else:
        status = "Crazy Rich 💎"
        color = discord.Color.purple()
        desc = "Hartamu tidak akan habis 7 turunan!"
    
    embed = discord.Embed(
        title=f"👛 Isi Dompet {interaction.user.display_name}",
        description=f"*{desc}*",
        color=color
    )
    embed.add_field(name="Saldo Koin", value=f"# 💰 {coins:,}", inline=False)
    embed.add_field(name="Status Ekonomi", value=f"**{status}**", inline=False)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.set_footer(text="Gunakan /daily untuk klaim koin harian!", icon_url="https://cdn-icons-png.flaticon.com/512/2933/2933116.png")
    await interaction.response.send_message(embed=embed)

# Command untuk klaim hadiah harian
@bot.tree.command(name="daily", description="Klaim hadiah koin harian Anda (cooldown 12 jam).")
async def daily(interaction: discord.Interaction):
    user_id = interaction.user.id
    user_data = await bot.db.get_user_data(user_id)
    
    # Tentukan waktu sekarang dengan timezone
    now = datetime.now(timezone.utc)
    cooldown = timedelta(hours=12)
    
    # Cek apakah user sudah pernah klaim sebelumnya
    if user_data['last_daily'] is not None:
        time_since_last_daily = now - user_data['last_daily']
        
        # Jika cooldown belum selesai
        if time_since_last_daily < cooldown:
            time_left = cooldown - time_since_last_daily
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            
            embed = discord.Embed(title="⏳ Cooldown Daily", description=f"Anda harus menunggu **{hours} jam {minutes} menit** lagi untuk bisa klaim hadiah harian.", color=discord.Color.orange())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    # Logika jika cooldown sudah selesai atau klaim pertama kali
    reward = random.randint(100, 500)
    # BUG FIX: Gunakan metode atomik untuk menambahkan koin dan mengupdate timestamp
    await bot.db.process_daily_claim(user_id, reward, now)
    
    embed = discord.Embed(
        title="📅 Hadiah Harian",
        description=f"Selamat {interaction.user.mention}, kamu telah mengklaim hadiah harianmu!",
        color=discord.Color.green()
    )
    embed.add_field(name="Diterima", value=f"**+{reward}** 💰", inline=True)
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/2933/2933116.png")
    await interaction.response.send_message(embed=embed)

# --- Fitur Ulang Tahun ---

# Membuat command group /birthday
birthday_group = app_commands.Group(name="birthday", description="Perintah terkait ulang tahun")

@birthday_group.command(name="set", description="Atur tanggal ulang tahunmu (format: DD-MM)")
@app_commands.describe(tanggal="Tanggal lahirmu dengan format DD-MM, contoh: 25-12")
async def set_birthday(interaction: discord.Interaction, tanggal: str):
    try:
        # Validasi format tanggal
        birth_date = datetime.strptime(tanggal, "%d-%m")
        # Simpan dalam format MM-DD untuk mempermudah query
        birthday_str_db = birth_date.strftime("%m-%d")
        
        await bot.db.set_birthday(interaction.user.id, birthday_str_db)
        
        embed = discord.Embed(title="✅ Berhasil", description=f"Ulang tahunmu berhasil diatur ke tanggal **{tanggal}**!", color=discord.Color.green())
        await send_auto_delete(interaction, embed=embed, delay=5)

    except ValueError:
        embed = discord.Embed(title="❌ Format Salah", description="Harap gunakan format **DD-MM**, contoh: `25-12`.", color=discord.Color.red())
        await send_auto_delete(interaction, embed=embed, delay=5)

@birthday_group.command(name="info", description="Lihat tanggal ulang tahun yang tersimpan")
async def info_birthday(interaction: discord.Interaction):
    user_data = await bot.db.get_user_data(interaction.user.id)
    birthday_str_db = user_data.get('birthday')

    if birthday_str_db:
        # Konversi dari MM-DD (DB) ke DD-MM (Display)
        birth_date = datetime.strptime(birthday_str_db, "%m-%d")
        display_date = birth_date.strftime("%d-%m")
        embed = discord.Embed(title="🎂 Info Ulang Tahun", description=f"Tanggal ulang tahunmu tercatat pada: **{display_date}**.", color=discord.Color.magenta())
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = discord.Embed(title="ℹ️ Info", description="Kamu belum mengatur tanggal ulang tahunmu. Gunakan `/birthday set`.", color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

# Daftarkan command group ke bot
bot.tree.add_command(birthday_group)

# --- Fitur Game ---

@bot.tree.command(name="tebakkata", description="Main tebak kata dari huruf yang diacak.")
async def tebak_kata(interaction: discord.Interaction):
    await bot.db.record_game_play(interaction.user.id, "Tebak Kata")
    kata_asli = random.choice(KATA_LIST)
    huruf_acak = ''.join(random.sample(kata_asli, len(kata_asli)))

    embed = discord.Embed(
        title="🔡 Tebak Kata!",
        description=f"Aku punya kata: **`{huruf_acak.upper()}`**\n\nCoba tebak kata aslinya apa? Kamu punya waktu 30 detik!",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

    def check(m):
        # Hanya cek pesan dari user yang menjalankan command di channel yang sama
        return m.author == interaction.user and m.channel == interaction.channel

    try:
        msg = await bot.wait_for('message', check=check, timeout=30.0)
        
        if msg.content.lower() == kata_asli:
            # BUG FIX: Gunakan add_coins untuk transaksi atomik
            await bot.db.add_coins(interaction.user.id, TEBAK_KATA_REWARD)
            embed = discord.Embed(title="🎉 Benar Sekali!", description=f"Jawabannya adalah **{kata_asli}**.\nKamu mendapatkan **{TEBAK_KATA_REWARD}** koin!", color=discord.Color.green())
            await send_auto_delete(interaction, embed=embed, delay=10, ephemeral=False)
        else:
            embed = discord.Embed(title="❌ Salah!", description=f"Jawaban yang benar adalah **{kata_asli}**. Coba lagi lain kali!", color=discord.Color.red())
            await send_auto_delete(interaction, embed=embed, delay=10, ephemeral=False)

    except asyncio.TimeoutError:
        embed = discord.Embed(title="⏰ Waktu Habis!", description=f"Jawaban yang benar adalah **{kata_asli}**.", color=discord.Color.red())
        await send_auto_delete(interaction, embed=embed, delay=10, ephemeral=False)

@bot.tree.command(name="mathbattle", description="Selesaikan soal matematika dalam 10 detik!")
async def math_battle(interaction: discord.Interaction):
    await bot.db.record_game_play(interaction.user.id, "Math Battle")
    ops = ['+', '-']
    op = random.choice(ops)
    num1 = random.randint(10, 99)
    num2 = random.randint(1, num1 if op == '-' else 99) # Pastikan hasil tidak negatif

    if op == '+':
        jawaban = num1 + num2
    else:
        jawaban = num1 - num2

    embed = discord.Embed(title="⚔️ Math Battle!", description=f"Berapa hasil dari **`{num1} {op} {num2}`**?\nWaktumu 10 detik!", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel

    try:
        msg = await bot.wait_for('message', check=check, timeout=10.0)
        
        if int(msg.content) == jawaban:
            # BUG FIX: Gunakan add_coins untuk transaksi atomik
            await bot.db.add_coins(interaction.user.id, MATH_BATTLE_REWARD)
            embed = discord.Embed(title="🧠 Cerdas!", description=f"Jawabannya **{jawaban}**.\nKamu dapat **{MATH_BATTLE_REWARD}** koin!", color=discord.Color.green())
            await send_auto_delete(interaction, embed=embed, delay=10, ephemeral=False)
        else:
            embed = discord.Embed(title="❌ Salah!", description=f"Jawaban yang benar adalah **{jawaban}**.", color=discord.Color.red())
            await send_auto_delete(interaction, embed=embed, delay=10, ephemeral=False)
    
    except asyncio.TimeoutError:
        embed = discord.Embed(title="⏰ Waktu Habis!", description=f"Jawabannya adalah **{jawaban}**.", color=discord.Color.red())
        await send_auto_delete(interaction, embed=embed, delay=10, ephemeral=False)
    except (ValueError, TypeError):
        embed = discord.Embed(title="❌ Error", description=f"Itu bukan angka! Jawaban yang benar adalah **{jawaban}**.", color=discord.Color.red())
        await send_auto_delete(interaction, embed=embed, delay=5, ephemeral=False)

@bot.tree.command(name="higherlower", description="Tebak angka rahasia antara 1-100.")
async def higher_lower(interaction: discord.Interaction):
    await bot.db.record_game_play(interaction.user.id, "Higher Lower")
    angka_rahasia = random.randint(1, 100)
    kesempatan = 5

    embed = discord.Embed(title="🤔 Higher or Lower", description=f"Aku telah memilih angka antara 1 dan 100.\nKamu punya **{kesempatan}** kesempatan untuk menebaknya!", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel

    for i in range(kesempatan):
        try:
            msg = await bot.wait_for('message', check=check, timeout=50.0)
            tebakan = int(msg.content)

            if tebakan == angka_rahasia:
                # BUG FIX: Gunakan add_coins untuk transaksi atomik
                await bot.db.add_coins(interaction.user.id, HIGHER_LOWER_REWARD)
                embed = discord.Embed(title="🏆 HEBAT!", description=f"Kamu berhasil menebak angkanya, yaitu **{angka_rahasia}**!\nKamu memenangkan **{HIGHER_LOWER_REWARD}** koin!", color=discord.Color.green())
                await send_auto_delete(interaction, embed=embed, delay=15, ephemeral=False)
                return # Keluar dari fungsi jika sudah menang
            
            elif tebakan < angka_rahasia:
                sisa_kesempatan = kesempatan - (i + 1)
                embed = discord.Embed(description=f"🔼 **Lebih Tinggi!** (Sisa kesempatan: {sisa_kesempatan})", color=discord.Color.orange())
                await send_auto_delete(interaction, embed=embed, delay=5, ephemeral=False)
            else:
                sisa_kesempatan = kesempatan - (i + 1)
                embed = discord.Embed(description=f"🔽 **Lebih Rendah!** (Sisa kesempatan: {sisa_kesempatan})", color=discord.Color.orange())
                await send_auto_delete(interaction, embed=embed, delay=5, ephemeral=False)

        except asyncio.TimeoutError:
            embed = discord.Embed(title="⏰ Waktu Habis!", description=f"Angka rahasianya adalah **{angka_rahasia}**.", color=discord.Color.red())
            await send_auto_delete(interaction, embed=embed, delay=10, ephemeral=False)
            return
        except (ValueError, TypeError):
            await send_auto_delete(interaction, "Itu bukan angka yang valid. Coba lagi.", delay=3, ephemeral=False)

    # Jika loop selesai tanpa menang
    embed = discord.Embed(title="💀 GAME OVER", description=f"Kamu kehabisan kesempatan. Angka rahasianya adalah **{angka_rahasia}**.", color=discord.Color.red())
    await send_auto_delete(interaction, embed=embed, delay=10, ephemeral=False)

# --- Game Batu Kertas Gunting (PvP) ---

class RPSBattleView(discord.ui.View):
    def __init__(self, player1: discord.User, player2: discord.User, bet: int):
        super().__init__(timeout=30.0)
        self.player1 = player1 # Initiator
        self.player2 = player2 # Opponent
        self.bet = bet
        self.choices = {player1.id: None, player2.id: None}
        self.message: discord.WebhookMessage = None # To store the original message

    async def on_timeout(self):
        # This method is called when the view times out.
        p1_choice = self.choices[self.player1.id]
        p2_choice = self.choices[self.player2.id]
        
        for item in self.children:
            item.disabled = True
        
        if p1_choice and not p2_choice:
            # Player 2 didn't choose, Player 1 wins by default
            await self.end_game(self.player1, self.player2, f"Waktu habis! {self.player2.mention} tidak memilih.")
        elif not p1_choice and p2_choice:
            # Player 1 didn't choose, Player 2 wins by default
            await self.end_game(self.player2, self.player1, f"Waktu habis! {self.player1.mention} tidak memilih.")
        else:
            # Both or neither chose, it's a draw, no coin transfer needed
            embed = discord.Embed(title="⏰ Waktu Habis", description="Permainan dibatalkan karena tidak ada pilihan.", color=discord.Color.red())
            await self.message.edit(content=None, embed=embed, view=self)

    async def handle_choice(self, interaction: discord.Interaction, choice: str):
        player = interaction.user

        if player.id not in self.choices:
            await send_auto_delete(interaction, "Kamu tidak ada dalam permainan ini!", delay=3)
            return

        if self.choices[player.id] is not None:
            await send_auto_delete(interaction, "Kamu sudah memilih!", delay=3)
            return

        self.choices[player.id] = choice
        await send_auto_delete(interaction, f"Kamu memilih **{choice}**. Menunggu lawan...", delay=5)

        p1_choice = self.choices[self.player1.id]
        p2_choice = self.choices[self.player2.id]

        if p1_choice and p2_choice:
            self.stop() # Stop the timeout countdown
            for item in self.children:
                item.disabled = True

            winner, loser = None, None
            if (p1_choice == "batu" and p2_choice == "gunting") or \
               (p1_choice == "kertas" and p2_choice == "batu") or \
               (p1_choice == "gunting" and p2_choice == "kertas"):
                winner, loser = self.player1, self.player2
            elif p1_choice != p2_choice:
                winner, loser = self.player2, self.player1

            if winner:
                await self.end_game(winner, loser, f"{winner.mention} memilih **{self.choices[winner.id]}** dan {loser.mention} memilih **{self.choices[loser.id]}**.")
            else: # Draw
                await self.message.edit(content=f"⚖️ **Seri!** Keduanya memilih **{p1_choice}**. Taruhan dikembalikan.", view=self)

    async def end_game(self, winner: discord.User, loser: discord.User, reason: str):
        # BUG FIX: Gunakan add_coins untuk transfer atomik
        await bot.db.add_coins(winner.id, self.bet)
        await bot.db.add_coins(loser.id, -self.bet)

        embed = discord.Embed(title="⚔️ Hasil Pertandingan", color=discord.Color.gold())
        embed.add_field(name="Pemenang", value=f"{winner.mention} 🏆", inline=True)
        embed.add_field(name="Hadiah", value=f"**{self.bet}** koin", inline=True)
        embed.description = f"{reason}\n\n{winner.mention} mengambil taruhan dari {loser.mention}!"
        await self.message.edit(content=None, embed=embed, view=self)

    @discord.ui.button(label="Batu 🗿", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "batu")

    @discord.ui.button(label="Kertas 📄", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "kertas")

    @discord.ui.button(label="Gunting ✂️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "gunting")


class RPSChallengeView(discord.ui.View):
    def __init__(self, initiator: discord.User, opponent: discord.User, bet: int):
        super().__init__(timeout=60.0)
        self.initiator = initiator
        self.opponent = opponent
        self.bet = bet
        self.message: discord.WebhookMessage = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await send_auto_delete(interaction, "Ini bukan tantangan untukmu!", delay=3)
            return False
        return True
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        embed = discord.Embed(title="⏰ Waktu Habis", description="Tantangan tidak direspons dan telah dibatalkan.", color=discord.Color.red())
        await self.message.edit(content=None, embed=embed, view=self)

    @discord.ui.button(label="Terima Tantangan", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        initiator_data = await bot.db.get_user_data(self.initiator.id)
        opponent_data = await bot.db.get_user_data(self.opponent.id)

        if initiator_data['coins'] < self.bet:
            embed = discord.Embed(title="❌ Gagal", description=f"Gagal memulai: {self.initiator.mention} tidak punya cukup koin lagi.", color=discord.Color.red())
            await interaction.response.edit_message(content=None, embed=embed, view=None)
            self.stop()
            return
        if opponent_data['coins'] < self.bet:
            embed = discord.Embed(title="❌ Gagal", description=f"Gagal memulai: Kamu tidak punya cukup koin untuk taruhan ini.", color=discord.Color.red())
            await interaction.response.edit_message(content=None, embed=embed, view=None)
            self.stop()
            return

        # Catat statistik game
        await bot.db.record_game_play(self.initiator.id, "Batu Gunting Kertas")
        await bot.db.record_game_play(self.opponent.id, "Batu Gunting Kertas")

        game_view = RPSBattleView(self.initiator, self.opponent, self.bet)
        embed = discord.Embed(title="⚔️ Pertarungan Dimulai!", description=f"{self.initiator.mention} vs {self.opponent.mention}\nSilakan pilih gerakan kalian. Waktu 30 detik!", color=discord.Color.gold())
        await interaction.response.edit_message(content=None, embed=embed, view=game_view)
        game_view.message = await interaction.original_response()
        self.stop()

    @discord.ui.button(label="Tolak", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="❌ Ditolak", description=f"{self.opponent.mention} menolak tantangan.", color=discord.Color.red())
        await interaction.response.edit_message(content=None, embed=embed, view=None)
        self.stop()


@bot.tree.command(name="rps", description="Tantang pemain lain untuk bermain Batu Kertas Gunting dengan taruhan.")
@app_commands.describe(
    lawan="Pemain yang ingin kamu tantang.",
    taruhan="Jumlah koin yang dipertaruhkan."
)
async def rps(interaction: discord.Interaction, lawan: discord.User, taruhan: app_commands.Range[int, 1]):
    initiator = interaction.user

    if initiator.id == lawan.id:
        embed = discord.Embed(description="❌ Kamu tidak bisa menantang dirimu sendiri!", color=discord.Color.red())
        await send_auto_delete(interaction, embed=embed, delay=5)
        return
    if lawan.bot:
        embed = discord.Embed(description="❌ Kamu tidak bisa menantang bot!", color=discord.Color.red())
        await send_auto_delete(interaction, embed=embed, delay=5)
        return

    initiator_data = await bot.db.get_user_data(initiator.id)
    if initiator_data['coins'] < taruhan:
        embed = discord.Embed(description=f"❌ Koinmu tidak cukup untuk bertaruh sebesar {taruhan} koin.", color=discord.Color.red())
        await send_auto_delete(interaction, embed=embed, delay=5)
        return

    opponent_data = await bot.db.get_user_data(lawan.id)
    if opponent_data['coins'] < taruhan:
        embed = discord.Embed(description=f"❌ {lawan.display_name} tidak memiliki cukup koin untuk taruhan ini.", color=discord.Color.red())
        await send_auto_delete(interaction, embed=embed, delay=5)
        return

    view = RPSChallengeView(initiator, lawan, taruhan)
    embed = discord.Embed(
        title="⚔️ Tantangan Batu Kertas Gunting! ⚔️",
        description=f"{initiator.mention} menantang {lawan.mention} untuk bermain dengan taruhan sebesar **{taruhan}** koin!",
        color=discord.Color.orange()
    )
    await interaction.response.send_message(content=f"Hei {lawan.mention}!", embed=embed, view=view)
    view.message = await interaction.original_response()

# --- Game Tic Tac Toe (XOXO) ---

class TicTacToeButton(discord.ui.Button):
    def __init__(self, x: int, y: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="\u200b", row=y)
        self.x = x
        self.y = y

    async def callback(self, interaction: discord.Interaction):
        view: TicTacToeView = self.view
        state = view.board[self.y][self.x]
        if state in (view.X, view.O):
            return

        if view.current_player == view.X:
            self.style = discord.ButtonStyle.danger
            self.label = 'X'
            self.disabled = True
            view.board[self.y][self.x] = view.X
            view.current_player = view.O
        else:
            self.style = discord.ButtonStyle.success
            self.label = 'O'
            self.disabled = True
            view.board[self.y][self.x] = view.O
            view.current_player = view.X

        winner = view.check_winner()
        description = ""
        color = discord.Color.blue()

        if winner is not None:
            if winner == view.X:
                description = f"🏆 {view.player1.mention} (X) Menang!"
                color = discord.Color.green()
            elif winner == view.O:
                description = f"🏆 {view.player2.mention} (O) Menang!"
                color = discord.Color.green()
            else:
                description = "🤝 **Seri!** Tidak ada pemenang."
                color = discord.Color.gold()

            for child in view.children:
                child.disabled = True

            view.stop()
        else:
            if view.current_player == view.X:
                description = f"Giliran {view.player1.mention} (X)"
            else:
                description = f"Giliran {view.player2.mention} (O)"

        embed = discord.Embed(title="🎮 Tic Tac Toe", description=description, color=color)
        await interaction.response.edit_message(content=None, embed=embed, view=view)

class TicTacToeView(discord.ui.View):
    X = -1
    O = 1
    def __init__(self, player1, player2):
        super().__init__()
        self.player1 = player1
        self.player2 = player2
        self.current_player = self.X
        self.board = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]

        for x in range(3):
            for y in range(3):
                self.add_item(TicTacToeButton(x, y))

    def check_winner(self):
        for across in self.board:
            value = sum(across)
            if value == 3: return self.O
            if value == -3: return self.X

        for line in range(3):
            value = self.board[0][line] + self.board[1][line] + self.board[2][line]
            if value == 3: return self.O
            if value == -3: return self.X

        diag = self.board[0][0] + self.board[1][1] + self.board[2][2]
        if diag == 3: return self.O
        if diag == -3: return self.X

        diag = self.board[0][2] + self.board[1][1] + self.board[2][0]
        if diag == 3: return self.O
        if diag == -3: return self.X

        if all(i != 0 for row in self.board for i in row):
            return 0 # Tie

        return None
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.player1 and interaction.user != self.player2:
            await interaction.response.send_message("Ini bukan permainanmu!", ephemeral=True)
            return False
        if interaction.user == self.player1 and self.current_player == self.O:
            await interaction.response.send_message("Bukan giliranmu!", ephemeral=True)
            return False
        if interaction.user == self.player2 and self.current_player == self.X:
            await interaction.response.send_message("Bukan giliranmu!", ephemeral=True)
            return False
        return True

# --- Mini Game Judi-Style ---

game_group = app_commands.Group(name="game", description="Perintah terkait mini-game judi.")


### GAME 1: RISK TOWER

class RiskTowerView(discord.ui.View):
    """UI untuk game Risk Tower dengan tombol Climb dan Cash Out."""
    def __init__(self, author: discord.User, bet: int):
        super().__init__(timeout=180.0)
        self.author = author
        self.bet = bet
        self.level = 0
        self.current_reward = 0
        # Definisikan setiap level: (peluang_sukses, multiplier)
        self.tower_levels = {
            1: (0.95, 1.2), 2: (0.90, 1.5), 3: (0.85, 2.0), 4: (0.80, 2.5),
            5: (0.70, 3.5), 6: (0.65, 5.0), 7: (0.60, 7.0), 8: (0.55, 10.0)
        }
        self.max_level = len(self.tower_levels)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await send_auto_delete(interaction, "Ini bukan permainanmu!", delay=3)
            return False
        return True

    def create_embed(self, status_message: str, is_game_over: bool = False) -> discord.Embed:
        """Membuat dan memformat embed untuk game."""
        tower_visual = []
        for i in range(self.max_level, 0, -1):
            chance, mult = self.tower_levels[i]
            reward = int(self.bet * mult)
            
            if i == self.level:
                # Level saat ini (baru saja dicapai)
                line = f"🧗 L{i} | x{mult:<3} | {reward} 💰 < KAMU"
            elif i < self.level:
                # Level yang sudah dilewati
                line = f"✅ L{i} | x{mult:<3} | LEWATI"
            else:
                # Level di atas
                line = f"🔒 L{i} | x{mult:<3} | {reward} 💰 ({int(chance*100)}%)"
            
            tower_visual.append(line)
        
        visual_str = "\n".join(tower_visual)
        visual_str += "\n➖➖➖➖➖➖➖➖➖➖\n🏁 DASAR MENARA"

        color = discord.Color.red() if "kalah" in status_message.lower() or "runtuh" in status_message.lower() else (discord.Color.green() if is_game_over else discord.Color.blue())
        
        embed = discord.Embed(title="🗼 RISK TOWER", description=status_message, color=color)
        embed.add_field(name="Menara", value=f"```\n{visual_str}\n```", inline=False)
        
        if not is_game_over:
            embed.add_field(name="💰 Cash Out Sekarang", value=f"**{self.current_reward}** koin", inline=True)
            if self.level < self.max_level:
                next_mult = self.tower_levels[self.level + 1][1]
                next_reward = int(self.bet * next_mult)
                embed.add_field(name="🚀 Hadiah Berikutnya", value=f"**{next_reward}** koin", inline=True)
        else:
            embed.add_field(name="Hasil Akhir", value=f"**{self.current_reward}** koin", inline=True)
            
        embed.set_footer(text=f"Player: {self.author.display_name} | Bet: {self.bet}")
        return embed

    async def end_game(self, interaction: discord.Interaction, message: str):
        """Menonaktifkan tombol dan mengakhiri game."""
        for item in self.children:
            item.disabled = True
        embed = self.create_embed(message, is_game_over=True)
        # Cek apakah interaksi sudah direspons sebelumnya untuk menghindari error
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Climb Higher", style=discord.ButtonStyle.primary, emoji="🧗")
    async def climb(self, interaction: discord.Interaction, button: discord.ui.Button):
        next_level = self.level + 1
        success_chance, multiplier = self.tower_levels[next_level]

        # Animasi suspense
        button.disabled = True
        await interaction.response.edit_message(embed=self.create_embed(f"Mencoba mendaki ke lantai {next_level}..."), view=self)
        await asyncio.sleep(2)

        if random.random() < success_chance: # Sukses
            self.level = next_level
            self.current_reward = int(self.bet * multiplier)
            
            if self.level == self.max_level: # Mencapai puncak
                # BUG FIX: Gunakan add_coins untuk memastikan saldo bertambah dengan benar
                await bot.db.add_coins(self.author.id, self.current_reward)
                await self.end_game(interaction, f"🏆 LUAR BIASA! Kamu mencapai puncak dan memenangkan **{self.current_reward}** koin!")
            else: # Lanjut
                button.disabled = False
                embed = self.create_embed(f"Sukses mencapai lantai {self.level}! Lanjut atau cash out?")
                await interaction.edit_original_response(embed=embed, view=self)
        else: # Gagal
            await self.end_game(interaction, f"💥 RUNTUH! Kamu jatuh dari lantai {self.level} dan kehilangan **{self.bet}** koin.")

    @discord.ui.button(label="Cash Out", style=discord.ButtonStyle.success, emoji="💰")
    async def cashout(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_reward > 0:
            # BUG FIX: Gunakan add_coins
            await bot.db.add_coins(self.author.id, self.current_reward)
            await self.end_game(interaction, f"✅ Aman! Kamu berhasil cash out dan mendapatkan **{self.current_reward}** koin.")
        else:
            await self.end_game(interaction, "Kamu turun tanpa membawa apa-apa.")

@game_group.command(name="risktower", description="Daki menara untuk hadiah besar, tapi hati-hati jangan sampai jatuh!")
@app_commands.describe(taruhan="Jumlah koin yang ingin dipertaruhkan.")
async def risk_tower(interaction: discord.Interaction, taruhan: app_commands.Range[int, 1]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await send_auto_delete(interaction, "❌ Koinmu tidak cukup untuk taruhan ini!", delay=5)
        return
 
    # Langsung potong taruhan menggunakan add_coins (negatif) agar lebih aman
    await bot.db.add_coins(interaction.user.id, -taruhan)
    await bot.db.record_game_play(interaction.user.id, "Risk Tower")

    view = RiskTowerView(interaction.user, taruhan)
    embed = view.create_embed("Selamat datang di Risk Tower! Tekan 'Climb' untuk memulai.")
    await interaction.response.send_message(embed=embed, view=view)


### GAME 2: ENERGY CORE

class EnergyCoreView(discord.ui.View):
    """UI untuk game Energy Core."""
    def __init__(self, author: discord.User, bet: int):
        super().__init__(timeout=180.0)
        self.author = author
        self.bet = bet
        self.charge = 0
        self.multiplier = 1.0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await send_auto_delete(interaction, "Ini bukan permainanmu!", delay=3)
            return False
        return True

    def create_embed(self, status: str) -> discord.Embed:
        progress = int((self.charge / 100) * 20)
        bar = '█' * progress + '░' * (20 - progress)
        color = discord.Color.yellow()
        if "meledak" in status.lower(): color = discord.Color.red()
        if "berhasil" in status.lower(): color = discord.Color.green()

        embed = discord.Embed(title="⚡ Energy Core", description=status, color=color)
        embed.add_field(name="Daya Inti", value=f"`{bar}` {self.charge}%", inline=False)
        embed.add_field(name="Taruhan", value=f"{self.bet} koin")
        embed.add_field(name="Multiplier", value=f"x{self.multiplier:.2f}")
        embed.add_field(name="Potensi Hadiah", value=f"**{int(self.bet * self.multiplier)} koin**")
        embed.set_footer(text=f"Bermain sebagai: {self.author.display_name}")
        return embed

    async def end_game(self, interaction: discord.Interaction, message: str):
        for item in self.children:
            item.disabled = True
        embed = self.create_embed(message)
        # Gunakan followup jika interaksi sudah direspons
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Charge Core", style=discord.ButtonStyle.primary, emoji="⚡")
    async def charge_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.children[0].disabled = True # Disable charge button
        self.children[1].disabled = True # Disable stop button
        await interaction.response.edit_message(view=self)

        await asyncio.sleep(1.5) # Suspense

        # Risiko meledak meningkat secara eksponensial
        overload_chance = (self.charge / 110) ** 2
        if random.random() < overload_chance:
            await self.end_game(interaction, f"💥 MELEDAK! Inti tidak stabil di {self.charge}% dan kamu kehilangan **{self.bet}** koin.")
            return

        self.charge += random.randint(5, 10)
        self.charge = min(self.charge, 100)
        self.multiplier += self.charge / 200 # Peningkatan multiplier

        if self.charge >= 100:
            reward = int(self.bet * self.multiplier)
            # BUG FIX: Gunakan add_coins
            await bot.db.add_coins(self.author.id, reward)
            await self.end_game(interaction, f"🔋 DAYA PENUH! Kamu berhasil mengumpulkan **{reward}** koin!")
        else:
            self.children[0].disabled = False
            self.children[1].disabled = False
            embed = self.create_embed("Daya meningkat! Lanjutkan atau berhenti?")
            await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Stop & Collect", style=discord.ButtonStyle.success, emoji="💸")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        reward = int(self.bet * self.multiplier)
        if reward > self.bet:
            await bot.db.add_coins(self.author.id, reward) # BUG FIX: Gunakan add_coins
            await self.end_game(interaction, f"✅ Berhasil! Kamu mengamankan inti dan mendapatkan **{reward}** koin.")
        else:
            # Kembalikan bet jika tidak ada profit
            user_data = await bot.db.get_user_data(self.author.id)
            await bot.db.update_user_balance(self.author.id, user_data['coins'] + self.bet)
            await self.end_game(interaction, "Kamu berhenti sebelum ada keuntungan. Taruhan dikembalikan.")

@game_group.command(name="energycore", description="Isi daya inti untuk multiplier, tapi jangan sampai meledak!")
@app_commands.describe(taruhan="Jumlah koin yang ingin dipertaruhkan.")
async def energy_core(interaction: discord.Interaction, taruhan: app_commands.Range[int, 1]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await send_auto_delete(interaction, "❌ Koinmu tidak cukup untuk taruhan ini!", delay=5)
        return
 
    # BUG FIX: Gunakan add_coins untuk transaksi atomik
    await bot.db.add_coins(interaction.user.id, -taruhan)
    await bot.db.record_game_play(interaction.user.id, "Energy Core")
    view = EnergyCoreView(interaction.user, taruhan)
    embed = view.create_embed("Inti energi stabil. Tekan 'Charge' untuk memulai.")
    await interaction.response.send_message(embed=embed, view=view)


### GAME 3: SHADOW DEAL

class ShadowDealView(discord.ui.View):
    """UI untuk game Shadow Deal."""
    def __init__(self, author: discord.User, bet: int):
        super().__init__(timeout=60.0)
        self.author = author
        self.bet = bet
        # [Kalah, Menang Kecil, Menang Besar]
        self.outcomes = [0, 1.5, 3.0]
        random.shuffle(self.outcomes)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await send_auto_delete(interaction, "Ini bukan permainanmu!", delay=3)
            return False
        return True

    async def reveal_sequence(self, interaction: discord.Interaction, choice_index: int):
        # Disable semua tombol
        for item in self.children:
            item.disabled = True

        # Animasi suspense
        await interaction.response.edit_message(content="*Kamu telah memilih... Sosok itu tersenyum di dalam bayangan...*", embed=None, view=self)
        await asyncio.sleep(2.5)

        # Tunjukkan salah satu kartu zonk yang tidak dipilih
        zonk_index = -1
        for i, outcome in enumerate(self.outcomes):
            if outcome == 0 and i != choice_index:
                zonk_index = i
                break
        
        if zonk_index != -1:
            await interaction.edit_original_response(content=f"*Dia membuka kartu lain... Kartu ke-{zonk_index + 1} ternyata **kosong**...*")
            await asyncio.sleep(3)

        # Hasil akhir
        final_outcome = self.outcomes[choice_index]
        reward = int(self.bet * final_outcome)

        if final_outcome == 0:
            embed = discord.Embed(title="🎭 Shadow Deal", description=f"🔮 Sosok itu membuka kartumu...\n# **ZONK** 💀\nKamu kehilangan **{self.bet}** koin.", color=discord.Color.dark_grey())
            await interaction.edit_original_response(content=None, embed=embed)
        else:
            # BUG FIX: Gunakan add_coins
            await bot.db.add_coins(self.author.id, reward)
            embed = discord.Embed(title="🎭 Shadow Deal", description=f"🔮 Sosok itu membuka kartumu...\n# **JACKPOT** 💎\nKamu memenangkan **{reward}** koin!", color=discord.Color.purple())
            await interaction.edit_original_response(content=None, embed=embed)
        self.stop()

    @discord.ui.button(label="Kartu Pertama", style=discord.ButtonStyle.secondary, emoji="🃏")
    async def card1(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.reveal_sequence(interaction, 0)

    @discord.ui.button(label="Kartu Kedua", style=discord.ButtonStyle.secondary, emoji="🃏")
    async def card2(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.reveal_sequence(interaction, 1)

    @discord.ui.button(label="Kartu Ketiga", style=discord.ButtonStyle.secondary, emoji="🃏")
    async def card3(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.reveal_sequence(interaction, 2)

@game_group.command(name="shadowdeal", description="Buat kesepakatan dengan bayangan, pilih satu dari tiga kartu.")
@app_commands.describe(taruhan="Jumlah koin yang ingin dipertaruhkan.")
async def shadow_deal(interaction: discord.Interaction, taruhan: app_commands.Range[int, 1]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await send_auto_delete(interaction, "❌ Koinmu tidak cukup untuk taruhan ini!", delay=5)
        return
 
    # BUG FIX: Gunakan add_coins untuk transaksi atomik
    await bot.db.add_coins(interaction.user.id, -taruhan)
    await bot.db.record_game_play(interaction.user.id, "Shadow Deal")
    view = ShadowDealView(interaction.user, taruhan)
    embed = discord.Embed(title="🎭 Shadow Deal", description=f"Sosok misterius muncul dari bayangan. Dia menawarimu sebuah permainan.\n\n\"Pilih satu dari tiga kartu ini,\" bisiknya. \"Nasibmu ada di tanganmu.\"\n\nKamu mempertaruhkan **{taruhan}** koin.", color=discord.Color.purple())
    await interaction.response.send_message(embed=embed, view=view)


### GAME 5: UNO (MULTIPLAYER)

class UnoCard:
    def __init__(self, color, value, card_type):
        self.color = color # red, green, blue, yellow, wild
        self.value = value # 0-9, None
        self.type = card_type # number, skip, reverse, draw2, wild, wild4

    def __repr__(self):
        color_map = {'red': '🟥', 'green': '🟩', 'blue': '🟦', 'yellow': '🟨', 'wild': '🌈'}
        type_map = {'skip': '🚫', 'reverse': '🔁', 'draw2': '⏫ +2', 'wild': 'Wild', 'wild4': '🍀 +4'}
        
        prefix = color_map.get(self.color, '')
        name = str(self.value) if self.type == 'number' else type_map.get(self.type, self.type)
        return f"{prefix} {name}"

class UnoGame:
    def __init__(self):
        self.deck = []
        self.discard_pile = []
        self.players = [] # List of discord.User
        self.hands = {} # {user_id: [UnoCard]}
        self.turn_index = 0
        self.direction = 1 # 1 or -1
        self.is_active = False
        self.bet = 0
        self.active_wild_color = None # State untuk warna wild yang aktif
        self.pot = 0
        self.winner = None
        self.last_action = "Permainan dimulai!"

    def create_deck(self):
        colors = ['red', 'green', 'blue', 'yellow']
        self.deck = []
        for color in colors:
            self.deck.append(UnoCard(color, 0, 'number'))
            for i in range(1, 10):
                self.deck.extend([UnoCard(color, i, 'number')] * 2)
            self.deck.extend([UnoCard(color, None, 'skip')] * 2)
            self.deck.extend([UnoCard(color, None, 'reverse')] * 2)
            self.deck.extend([UnoCard(color, None, 'draw2')] * 2)
        
        for _ in range(4):
            self.deck.append(UnoCard('wild', None, 'wild'))
            self.deck.append(UnoCard('wild', None, 'wild4'))
        
        random.shuffle(self.deck)

    def draw_card(self, count=1):
        drawn = []
        for _ in range(count):
            if not self.deck:
                if not self.discard_pile:
                    break # No cards left
                # Reshuffle discard into deck (keep top card)
                top_card = self.discard_pile.pop()
                self.deck = self.discard_pile
                self.discard_pile = [top_card]
                random.shuffle(self.deck)
            
            if self.deck:
                drawn.append(self.deck.pop())
        return drawn

    def can_play(self, card, top_card):
        # Kartu wild selalu bisa dimainkan
        if card.color == 'wild':
            return True
        
        # Tentukan warna efektif dari kartu teratas (memperhitungkan kartu wild yang sudah dipilih warnanya)
        effective_top_color = self.active_wild_color if top_card.color == 'wild' else top_card.color
        
        # Aturan 1: Warna cocok
        if card.color == effective_top_color:
            return True
        
        # Aturan 2: Angka atau Tipe cocok (tidak berlaku jika kartu atas adalah wild)
        if top_card.color != 'wild':
            if card.type == 'number' and card.value == top_card.value:
                return True
            if card.type != 'number' and card.type == top_card.type:
                return True
                
        return False

    def next_turn(self):
        self.turn_index = (self.turn_index + self.direction) % len(self.players)

class UnoPlayView(discord.ui.View):
    def __init__(self, game: UnoGame, main_view):
        super().__init__(timeout=60)
        self.game = game
        self.main_view = main_view
        self.selected_card_index = None

        # Setup Select Menu for cards
        current_player = game.players[game.turn_index]
        hand = game.hands[current_player.id]
        top_card = game.discard_pile[-1]
        
        options = []
        playable_indices = []
        
        for i, card in enumerate(hand):
            if game.can_play(card, top_card):
                label = str(card)
                # Handle duplicate display names in select menu
                options.append(discord.SelectOption(label=label, value=str(i), description=f"Kartu ke-{i+1}"))
                playable_indices.append(i)
        
        # Batasi opsi max 25 (limit Discord)
        if len(options) > 25:
            options = options[:25]
 
        if options:
            select = discord.ui.Select(placeholder="Pilih kartu untuk dimainkan...", options=options)
            select.callback = self.play_card_callback
            self.add_item(select)
        else:
            self.add_item(discord.ui.Button(label="Tidak ada kartu yang bisa dimainkan", disabled=True, style=discord.ButtonStyle.secondary))

    async def play_card_callback(self, interaction: discord.Interaction):
        index = int(interaction.data['values'][0])
        card = self.game.hands[interaction.user.id][index]

        if card.color == 'wild':
            # Jika Wild, tanya warna
            self.selected_card_index = index
            await interaction.response.edit_message(content="Pilih warna untuk kartu Wild:", view=UnoColorView(self.game, self.main_view, index))
        else:
            # Defer interaksi untuk memberi waktu pada logika game
            await interaction.response.defer(ephemeral=True)
            await self.main_view.process_move(interaction, index)

    @discord.ui.button(label="Ambil Kartu (Draw)", style=discord.ButtonStyle.secondary, emoji="🃏")
    async def draw_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await self.main_view.process_draw(interaction)

class UnoColorView(discord.ui.View):
    def __init__(self, game, main_view, card_index):
        super().__init__(timeout=60)
        self.game = game
        self.main_view = main_view
        self.card_index = card_index

    async def set_color(self, interaction: discord.Interaction, color: str):
        # Defer interaksi tombol warna untuk mencegah timeout
        await interaction.response.defer(ephemeral=True)
        await self.main_view.process_move(interaction, self.card_index, chosen_color=color)

    @discord.ui.button(label="Merah", style=discord.ButtonStyle.danger)
    async def red(self, interaction: discord.Interaction, button: discord.ui.Button): await self.set_color(interaction, 'red')
    @discord.ui.button(label="Hijau", style=discord.ButtonStyle.success)
    async def green(self, interaction: discord.Interaction, button: discord.ui.Button): await self.set_color(interaction, 'green')
    @discord.ui.button(label="Biru", style=discord.ButtonStyle.primary)
    async def blue(self, interaction: discord.Interaction, button: discord.ui.Button): await self.set_color(interaction, 'blue')
    @discord.ui.button(label="Kuning", style=discord.ButtonStyle.secondary) # Discord gak punya kuning, pakai secondary/grey
    async def yellow(self, interaction: discord.Interaction, button: discord.ui.Button): await self.set_color(interaction, 'yellow')

class UnoGameView(discord.ui.View):
    def __init__(self, game: UnoGame):
        super().__init__(timeout=600) # 10 menit timeout game
        self.game = game
        self.message = None

    async def _delete_msg_after(self, message: discord.WebhookMessage, delay: float):
        """Helper non-blocking task untuk menghapus pesan setelah jeda waktu."""
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except (discord.NotFound, discord.HTTPException):
            pass # Abaikan jika pesan sudah hilang atau ada error lain.

    def update_embed(self):
        top_card = self.game.discard_pile[-1]
        current_player = self.game.players[self.game.turn_index]
        
        desc =  f"**Aksi Terakhir:** *{self.game.last_action}*\n\n"
        desc += f"**Giliran Saat Ini:** {current_player.mention}\n"
        desc += f"**Arah Giliran:** {'Searah Jarum Jam' if self.game.direction == 1 else 'Berlawanan Arah'}\n"
        desc += f"**Pot:** {self.game.pot} koin\n\n"
        desc += f"**Kartu Atas:**\n# {top_card}\n\n"
        
        desc += "**Sisa Kartu:**\n"
        for p in self.game.players:
            count = len(self.game.hands[p.id])
            status = "🎮" if p.id == current_player.id else "👤"
            desc += f"{status} {p.display_name}: **{count}** kartu\n"

        color_map = {'red': discord.Color.red(), 'green': discord.Color.green(), 'blue': discord.Color.blue(), 'yellow': discord.Color.gold(), 'wild': discord.Color.purple()}
        embed = discord.Embed(title="🎮 UNO Game", description=desc, color=color_map.get(top_card.color, discord.Color.default()))
        return embed

    async def process_draw(self, interaction: discord.Interaction):
        player = interaction.user
        drawn = self.game.draw_card(1)
        if drawn:
            self.game.last_action = f"{player.mention} mengambil satu kartu."
            self.game.hands[player.id].extend(drawn)
            msg = await interaction.followup.send(f"Kamu mengambil: {drawn[0]}", ephemeral=True)
            asyncio.create_task(self._delete_msg_after(msg, 5))
        else:
            self.game.last_action = f"{player.mention} mencoba mengambil kartu, tapi deck kosong."
            msg = await interaction.followup.send("Deck habis!", ephemeral=True)
            asyncio.create_task(self._delete_msg_after(msg, 5))
        
        self.game.next_turn()
        next_player = self.game.players[self.game.turn_index]
        await self.message.edit(content=f"Giliranmu, {next_player.mention}!", embed=self.update_embed(), view=self)

    async def process_move(self, interaction: discord.Interaction, card_index, chosen_color=None):
        player = interaction.user
        card = self.game.hands[player.id].pop(card_index)
        
        # Atur state warna wild, jangan ubah properti kartu aslinya
        self.game.active_wild_color = chosen_color if card.color == 'wild' else None
        
        self.game.discard_pile.append(card)
        
        if chosen_color:
            msg = f"{player.mention} memainkan **{card}** dan memilih warna **{chosen_color.capitalize()}**."
        else:
            msg = f"{player.mention} memainkan **{card}**."
        
        # Cek Menang
        if len(self.game.hands[player.id]) == 0:
            # WINNER
            # BUG FIX: Gunakan add_coins
            await bot.db.add_coins(player.id, self.game.pot)
            
            embed = discord.Embed(title="🏆 UNO WINNER!", description=f"Selamat {player.mention}! Kamu memenangkan permainan dan mengambil seluruh pot sebesar **{self.game.pot}** koin!", color=discord.Color.gold())
            await self.message.edit(content=None, embed=embed, view=None)
            self.stop()
            msg = await interaction.followup.send("Permainan selesai! Pesan ini akan hilang.", ephemeral=True)
            asyncio.create_task(self._delete_msg_after(msg, 3))
            return

        # Efek Spesial
        if card.type == 'skip':
            self.game.next_turn() # Skip next player
            msg += " Giliran selanjutnya dilewati!"
        elif card.type == 'reverse':
            self.game.direction *= -1
            if len(self.game.players) == 2: # Reverse di 2 pemain = Skip
                self.game.next_turn()
            msg += " Arah permainan berbalik!"
        elif card.type == 'draw2':
            next_p_idx = (self.game.turn_index + self.game.direction) % len(self.game.players)
            next_p = self.game.players[next_p_idx]
            drawn = self.game.draw_card(2)
            self.game.hands[next_p.id].extend(drawn)
            self.game.next_turn() # Skip player yang draw
            msg += f" {next_p.mention} mengambil 2 kartu dan dilewati!"
        elif card.type == 'wild4':
            next_p_idx = (self.game.turn_index + self.game.direction) % len(self.game.players)
            next_p = self.game.players[next_p_idx]
            drawn = self.game.draw_card(4)
            self.game.hands[next_p.id].extend(drawn)
            self.game.next_turn() # Skip player yang draw
            msg += f" {next_p.mention} mengambil 4 kartu dan dilewati!"
        
        self.game.last_action = msg
        self.game.next_turn()
        next_player = self.game.players[self.game.turn_index]
        await self.message.edit(content=f"Giliranmu, {next_player.mention}!", embed=self.update_embed(), view=self)
        msg = await interaction.followup.send("Kartu dimainkan.", ephemeral=True)
        asyncio.create_task(self._delete_msg_after(msg, 3))

    @discord.ui.button(label="Mainkan Giliran", style=discord.ButtonStyle.primary)
    async def play_turn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.players[self.game.turn_index].id:
            await interaction.response.send_message("Bukan giliranmu!", ephemeral=True)
            return
        
        # Buat view dengan opsi yang bisa dimainkan
        play_view = UnoPlayView(self.game, self)

        # Buat pesan yang lebih informatif
        hand = self.game.hands[interaction.user.id]
        hand_str = " | ".join(str(c) for c in hand) if hand else "Tidak ada kartu."
        
        embed = discord.Embed(title="Giliranmu!", color=interaction.user.color)
        embed.description = "**Kartu di Tanganmu:**\n" + hand_str
        embed.set_footer(text="Pilih kartu yang bisa dimainkan dari menu di bawah, atau ambil kartu baru.")

        await interaction.response.send_message(embed=embed, view=play_view, ephemeral=True)

    @discord.ui.button(label="Lihat Kartu Saya", style=discord.ButtonStyle.secondary, emoji="🎴")
    async def view_cards_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.game.hands:
            await send_auto_delete(interaction, "Kamu tidak ada dalam permainan ini!", delay=3, ephemeral=True)
            return

        hand = self.game.hands.get(interaction.user.id, [])
        hand_str = " | ".join(str(c) for c in hand) if hand else "Kamu tidak punya kartu."
        
        embed = discord.Embed(title="Kartu di Tanganmu", description=hand_str, color=interaction.user.color)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Menyerah", style=discord.ButtonStyle.danger, emoji="🏳️")
    async def surrender_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in self.game.hands:
            await send_auto_delete(interaction, "Kamu tidak ada dalam permainan ini!", delay=3, ephemeral=True)
            return

        player = interaction.user
        
        # Cari index player
        player_idx = -1
        for i, p in enumerate(self.game.players):
            if p.id == player.id:
                player_idx = i
                break
        
        if player_idx == -1: return

        # Hapus player
        removed_player = self.game.players.pop(player_idx)
        del self.game.hands[removed_player.id]

        # Adjust turn index
        if player_idx < self.game.turn_index:
            self.game.turn_index -= 1
        elif player_idx == self.game.turn_index:
            if self.game.direction == -1:
                self.game.turn_index -= 1
        
        if self.game.players:
            self.game.turn_index %= len(self.game.players)

        if len(self.game.players) == 1:
            winner = self.game.players[0]
            # BUG FIX: Gunakan add_coins
            await bot.db.add_coins(winner.id, self.game.pot)
            
            embed = discord.Embed(title="🏆 UNO WINNER!", description=f"{removed_player.mention} menyerah!\nSelamat {winner.mention}! Kamu memenangkan permainan dan mengambil seluruh pot sebesar **{self.game.pot}** koin!", color=discord.Color.gold())
            await self.message.edit(content=None, embed=embed, view=None)
            self.stop()
            await send_auto_delete(interaction, "Kamu menyerah.", delay=3, ephemeral=True)
            return

        self.game.last_action = f"{removed_player.mention} menyerah dan keluar."
        next_player = self.game.players[self.game.turn_index]
        await self.message.edit(content=f"Giliranmu, {next_player.mention}!", embed=self.update_embed(), view=self)
        await send_auto_delete(interaction, "Kamu telah menyerah dari permainan.", delay=3, ephemeral=True)

class UnoLobbyView(discord.ui.View):
    def __init__(self, host, bet):
        super().__init__(timeout=300)
        self.host = host
        self.bet = bet
        self.players = [host]

    @discord.ui.button(label="Join Game", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user in self.players:
            await interaction.response.send_message("Kamu sudah bergabung!", ephemeral=True)
            return
        if len(self.players) >= 4:
            await interaction.response.send_message("Lobby penuh!", ephemeral=True)
            return
        
        user_data = await bot.db.get_user_data(interaction.user.id)
        if user_data['coins'] < self.bet:
            await interaction.response.send_message(f"Koinmu tidak cukup! Butuh {self.bet} koin.", ephemeral=True)
            return

        self.players.append(interaction.user)
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Mulai Game", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        # FIX: Gunakan ID untuk membandingkan host
        if interaction.user.id != self.host.id:
            await send_auto_delete(interaction, "Hanya host yang bisa memulai game.", delay=3, ephemeral=True)
            return
        if len(self.players) < 2:
            await send_auto_delete(interaction, "Butuh minimal 2 pemain!", delay=3, ephemeral=True)
            return

        # Potong saldo semua pemain
        pot = 0
        for p in self.players:
            # BUG FIX: Gunakan add_coins untuk transaksi atomik
            await bot.db.add_coins(p.id, -self.bet)
            pot += self.bet
            await bot.db.record_game_play(p.id, "UNO")

        # Setup Game
        game = UnoGame()
        game.players = self.players
        game.bet = self.bet
        game.pot = pot
        game.create_deck()
        
        # Deal cards
        for p in game.players:
            game.hands[p.id] = game.draw_card(7)
        
        # Start card (cannot be wild for simplicity logic here, or handle it)
        while True:
            start_card = game.draw_card(1)[0]
            if start_card.color != 'wild':
                game.discard_pile.append(start_card)
                break
            game.deck.append(start_card) # Return wild and shuffle if needed, simplified just append back
            random.shuffle(game.deck)

        game_view = UnoGameView(game)
        embed = game_view.update_embed()
        first_player = game.players[game.turn_index]
        await interaction.response.edit_message(content=f"Game Dimulai! Giliran pertama: {first_player.mention}", embed=embed, view=game_view)
        game_view.message = await interaction.original_response()


    def create_embed(self):
        embed = discord.Embed(title="UNO Lobby", description=f"Host: {self.host.mention}\nTaruhan: **{self.bet}** koin\n\n**Pemain ({len(self.players)}/4):**", color=discord.Color.orange())
        for p in self.players:
            embed.add_field(name=p.display_name, value="Ready", inline=False)
        return embed

@game_group.command(name="uno", description="Mainkan UNO multiplayer dengan taruhan!")
@app_commands.describe(taruhan="Jumlah koin untuk bergabung.")
async def play_uno(interaction: discord.Interaction, taruhan: app_commands.Range[int, 10]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await send_auto_delete(interaction, "Koinmu tidak cukup untuk membuat lobby ini.", delay=5, ephemeral=True)
        return

    view = UnoLobbyView(interaction.user, taruhan)
    await interaction.response.send_message(embed=view.create_embed(), view=view)

### GAME 4: SLOT MACHINE

class SlotMachineView(discord.ui.View):
    def __init__(self, author: discord.User, bet: int):
        super().__init__(timeout=180.0)
        self.author = author
        self.bet = bet
        self.reels = ['❓', '❓', '❓']
        self.emojis = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '💰', '🍀']
        # Payouts: {emoji: {count: multiplier}}
        self.payouts = {
            '🍒': {2: 1.5, 3: 3}, '🍋': {2: 1.5, 3: 3},
            '🍊': {2: 2, 3: 4}, '🍇': {2: 2.5, 3: 5},
            '🍀': {2: 3, 3: 7}, '🔔': {2: 5, 3: 15},
            '💎': {2: 10, 3: 30}, '💰': {2: 25, 3: 77},
        }

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await send_auto_delete(interaction, "Ini bukan permainanmu!", delay=3)
            return False
        return True

    def create_embed(self, status: str, win_amount: int = 0) -> discord.Embed:
        reel_display = ' | '.join(self.reels)
        color = discord.Color.green() if win_amount > 0 else (discord.Color.dark_grey() if "kalah" in status.lower() else discord.Color.blue())
        
        embed = discord.Embed(title="🎰 Mesin Slot 🎰", color=color)
        embed.description = f"# ▸ {reel_display} ◂\n\n{status}"
        embed.add_field(name="Taruhan", value=f"{self.bet} koin")
        if win_amount > 0:
            embed.add_field(name="Kemenangan", value=f"**{win_amount} koin**")
        embed.set_footer(text=f"Bermain sebagai: {self.author.display_name}")
        return embed

    async def spin_logic(self, interaction: discord.Interaction):
        await interaction.response.defer()
        spin_button = self.children[0]
        spin_button.disabled = True
        await interaction.edit_original_response(view=self)

        user_data = await bot.db.get_user_data(self.author.id)
        if user_data['coins'] < self.bet:
            for item in self.children: item.disabled = True
            await interaction.edit_original_response(content="❌ Koinmu tidak cukup untuk memutar lagi.", embed=None, view=self)
            self.stop()
            return
        # BUG FIX: Gunakan add_coins
        await bot.db.add_coins(self.author.id, -self.bet)
        await bot.db.record_game_play(self.author.id, "Slot Machine")

        for _ in range(3):
            self.reels = [random.choice(self.emojis) for _ in range(3)]
            await asyncio.sleep(0.4)

        counts = {emoji: self.reels.count(emoji) for emoji in set(self.reels)}
        win_amount = 0
        status = f"Kamu kalah dan kehilangan **{self.bet}** koin."

        winning_emoji = next((e for e, c in counts.items() if c == 3), None) or \
                        next((e for e, c in counts.items() if c == 2), None)
        
        if winning_emoji:
            count = counts[winning_emoji]
            multiplier = self.payouts.get(winning_emoji, {}).get(count, 0)
            if multiplier > 0:
                win_amount = int(self.bet * multiplier)
                status = f"🎉 **JACKPOT!** Kamu memenangkan **{win_amount}** koin!"
                # BUG FIX: Gunakan add_coins
                await bot.db.add_coins(self.author.id, win_amount)
        
        embed = self.create_embed(status, win_amount)
        spin_button.disabled = False
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Putar Lagi", style=discord.ButtonStyle.primary, emoji="▶️")
    async def spin_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.spin_logic(interaction)

    @discord.ui.button(label="Berhenti", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="Permainan dihentikan.", embed=None, view=self)
        self.stop()

@game_group.command(name="slotmachine", description="Mainkan mesin slot dan menangkan hadiah besar!")
@app_commands.describe(taruhan="Jumlah koin yang ingin dipertaruhkan per putaran.")
async def slot_machine(interaction: discord.Interaction, taruhan: app_commands.Range[int, 1]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await send_auto_delete(interaction, "❌ Koinmu tidak cukup untuk taruhan awal ini!", delay=5)
        return
    
    view = SlotMachineView(interaction.user, taruhan)
    embed = view.create_embed("Selamat datang! Tekan 'Putar Lagi' untuk memulai permainan.")
    await interaction.response.send_message(embed=embed, view=view)

@game_group.command(name="guessnumber", description="Tebak angka 1-10 dan menangkan 5x lipat taruhanmu!")
@app_commands.describe(
    taruhan="Jumlah koin yang ingin dipertaruhkan.",
    tebakan="Tebakan angkamu dari 1 sampai 10."
)
async def guess_number(interaction: discord.Interaction, taruhan: app_commands.Range[int, 1], tebakan: app_commands.Range[int, 1, 10]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await send_auto_delete(interaction, "❌ Koinmu tidak cukup untuk taruhan ini!", delay=5)
        return

    # BUG FIX: Gunakan add_coins untuk transaksi atomik
    await bot.db.add_coins(interaction.user.id, -taruhan)
    await bot.db.record_game_play(interaction.user.id, "Tebak Angka")

    angka_bot = random.randint(1, 10)
    reward_multiplier = 5
    reward = taruhan * reward_multiplier

    if tebakan == angka_bot:
        # BUG FIX: Gunakan add_coins untuk menambahkan hadiah
        await bot.db.add_coins(interaction.user.id, reward)
        embed = discord.Embed(title="🎉 JACKPOT! 🎉", description=f"Tebakanmu **{tebakan}** benar! Angka rahasianya adalah **{angka_bot}**.\nKamu memenangkan **{reward}** koin!", color=discord.Color.green())
    else:
        # Kalah, taruhan sudah dipotong
        embed = discord.Embed(title="💥 ZONK! 💥", description=f"Tebakanmu **{tebakan}** salah. Angka rahasianya adalah **{angka_bot}**.\nKamu kehilangan **{taruhan}** koin.", color=discord.Color.red())
    
    await interaction.response.send_message(embed=embed)

### GAME 6: BLACKJACK

class BlackjackView(discord.ui.View):
    def __init__(self, author: discord.User, bet: int):
        super().__init__(timeout=180.0)
        self.author = author
        self.bet = bet
        self.deck = []
        self.player_hand = []
        self.dealer_hand = []
        self.create_deck()
        self.deal_initial()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await send_auto_delete(interaction, "Ini bukan permainanmu!", delay=3)
            return False
        return True

    def create_deck(self):
        suits = ['♠️', '♥️', '♣️', '♦️']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        self.deck = [(rank, suit) for suit in suits for rank in ranks]
        random.shuffle(self.deck)

    def draw_card(self):
        return self.deck.pop()

    def deal_initial(self):
        self.player_hand = [self.draw_card(), self.draw_card()]
        self.dealer_hand = [self.draw_card(), self.draw_card()]

    def calculate_score(self, hand):
        score = 0
        aces = 0
        for rank, _ in hand:
            if rank in ['J', 'Q', 'K']:
                score += 10
            elif rank == 'A':
                aces += 1
                score += 11
            else:
                score += int(rank)
        
        while score > 21 and aces:
            score -= 10
            aces -= 1
        return score

    def format_hand(self, hand, hide_second=False):
        cards_str = ""
        for i, (rank, suit) in enumerate(hand):
            if hide_second and i == 1:
                cards_str += "🂠 "
            else:
                cards_str += f"[`{rank}{suit}`] "
        return cards_str

    def create_embed(self, result=None):
        player_score = self.calculate_score(self.player_hand)
        
        if result: # Game over, show dealer
            dealer_score = self.calculate_score(self.dealer_hand)
            dealer_hand_str = self.format_hand(self.dealer_hand)
            dealer_title = f"Dealer: {dealer_score}"
        else: # Game ongoing, hide dealer 2nd card
            dealer_hand_str = self.format_hand(self.dealer_hand, hide_second=True)
            dealer_title = "Dealer: ?"

        color = discord.Color.blue()
        if result:
            if "Menang" in result or "Blackjack" in result: color = discord.Color.green()
            elif "Kalah" in result or "Bust" in result: color = discord.Color.red()
            elif "Seri" in result: color = discord.Color.gold()

        embed = discord.Embed(title="🃏 Blackjack", description=result, color=color)
        embed.add_field(name=f"Kartumu ({player_score})", value=self.format_hand(self.player_hand), inline=True)
        embed.add_field(name=dealer_title, value=dealer_hand_str, inline=True)
        embed.add_field(name="Taruhan", value=f"{self.bet} koin", inline=False)
        embed.set_footer(text=f"Player: {self.author.display_name}")
        return embed

    async def end_game(self, interaction: discord.Interaction, result: str, payout_mult: float = 0):
        for item in self.children: item.disabled = True
        
        if payout_mult > 0:
            # payout_mult 1.0 = balik modal (seri), 2.0 = menang 1x, 2.5 = blackjack
            payout = int(self.bet * payout_mult)
            await bot.db.add_coins(self.author.id, payout)
        
        embed = self.create_embed(result)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player_hand.append(self.draw_card())
        score = self.calculate_score(self.player_hand)
        
        if score > 21:
            await self.end_game(interaction, "💥 BUST! Kamu melebihi 21. Kamu kalah.", 0)
        elif score == 21:
            await self.stand_logic(interaction)
        else:
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.stand_logic(interaction)

    async def stand_logic(self, interaction: discord.Interaction):
        player_score = self.calculate_score(self.player_hand)
        
        while self.calculate_score(self.dealer_hand) < 17:
            self.dealer_hand.append(self.draw_card())
        
        dealer_score = self.calculate_score(self.dealer_hand)
        
        if dealer_score > 21:
            await self.end_game(interaction, "🎉 Dealer BUST! Kamu Menang!", 2.0)
        elif dealer_score > player_score:
            await self.end_game(interaction, "❌ Dealer memiliki nilai lebih tinggi. Kamu Kalah.", 0)
        elif dealer_score < player_score:
            await self.end_game(interaction, "🎉 Nilaimu lebih tinggi! Kamu Menang!", 2.0)
        else:
            await self.end_game(interaction, "⚖️ Seri (Push). Taruhan dikembalikan.", 1.0)

@game_group.command(name="blackjack", description="Main Blackjack (21) melawan dealer.")
@app_commands.describe(taruhan="Jumlah koin yang ingin dipertaruhkan.")
async def blackjack(interaction: discord.Interaction, taruhan: app_commands.Range[int, 1]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await send_auto_delete(interaction, "❌ Koinmu tidak cukup!", delay=5)
        return
    
    # Potong taruhan di awal
    await bot.db.add_coins(interaction.user.id, -taruhan)
    await bot.db.record_game_play(interaction.user.id, "Blackjack")
    
    view = BlackjackView(interaction.user, taruhan)
    
    # Cek Instant Blackjack Player
    if view.calculate_score(view.player_hand) == 21:
        if view.calculate_score(view.dealer_hand) == 21:
             await bot.db.add_coins(interaction.user.id, taruhan) # Refund
             embed = view.create_embed("⚖️ Keduanya Blackjack! Seri.")
             await interaction.response.send_message(embed=embed, view=None)
        else:
             payout = int(taruhan * 2.5) # Menang 3:2
             await bot.db.add_coins(interaction.user.id, payout)
             embed = view.create_embed("🎉 BLACKJACK! Kamu menang 1.5x lipat!")
             await interaction.response.send_message(embed=embed, view=None)
        return

    await interaction.response.send_message(embed=view.create_embed(), view=view)

### GAME 7: BALAPAN (RACE)
@game_group.command(name="balapan", description="Taruhan pada balapan hewan! Pilih jagoanmu.")
@app_commands.describe(
    taruhan="Jumlah koin yang dipertaruhkan.",
    jagoan="Pilih hewan jagoanmu (1-4)."
)
@app_commands.choices(jagoan=[
    app_commands.Choice(name="1. 🐎 Kuda", value=1),
    app_commands.Choice(name="2. 🐕 Anjing", value=2),
    app_commands.Choice(name="3. 🐈 Kucing", value=3),
    app_commands.Choice(name="4. 🐇 Kelinci", value=4)
])
async def balapan(interaction: discord.Interaction, taruhan: app_commands.Range[int, 1], jagoan: app_commands.Choice[int]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await send_auto_delete(interaction, "❌ Koinmu tidak cukup!", delay=5)
        return

    # Potong taruhan
    await bot.db.add_coins(interaction.user.id, -taruhan)
    await bot.db.record_game_play(interaction.user.id, "Balapan")

    runners = [
        {"emoji": "🐎", "name": "Kuda", "pos": 0},
        {"emoji": "🐕", "name": "Anjing", "pos": 0},
        {"emoji": "🐈", "name": "Kucing", "pos": 0},
        {"emoji": "🐇", "name": "Kelinci", "pos": 0}
    ]
    
    track_length = 15
    
    embed = discord.Embed(title="🏁 Balapan Dimulai! 🏁", description="Para peserta bersiap di garis start...", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    
    winner_idx = -1
    
    while winner_idx == -1:
        await asyncio.sleep(1.5)
        
        track_display = ""
        finished_runners = []
        
        for i, runner in enumerate(runners):
            # Gerakan acak 1-3 langkah
            move = random.randint(1, 3)
            # Sedikit variasi acak agar tidak monoton (10% chance boost)
            if random.random() < 0.1: move += 1
            
            runner["pos"] += move
            
            # Visualisasi Track
            # Clamp posisi untuk visual agar tidak melebihi panjang track
            visual_pos = min(runner["pos"], track_length)
            
            spaces_before = visual_pos
            spaces_after = track_length - visual_pos
            
            line = "🏁 " + "・" * spaces_before + runner["emoji"] + "・" * spaces_after + " 🏁"
            
            if runner["pos"] >= track_length:
                finished_runners.append(i)
                line += " 🚩"
            
            track_display += f"**{i+1}. {runner['name']}**\n{line}\n\n"
        
        embed.description = track_display
        await msg.edit(embed=embed)
        
        if finished_runners:
            # Jika ada yang finish, tentukan pemenang
            # Jika seri (finish bareng), ambil yang posisinya paling jauh
            winner_idx = max(finished_runners, key=lambda i: runners[i]["pos"])
            break
            
    # Hasil Akhir
    winner = runners[winner_idx]
    user_choice_idx = jagoan.value - 1
    
    result_desc = f"🏆 **{winner['name']}** ({winner['emoji']}) memenangkan balapan!\n\n"
    
    if user_choice_idx == winner_idx:
        winnings = taruhan * 3 # Menang 3x lipat (karena ada 4 peserta)
        await bot.db.add_coins(interaction.user.id, winnings)
        result_desc += f"🎉 **SELAMAT!** Pilihanmu tepat! Kamu memenangkan **{winnings}** koin!"
        color = discord.Color.green()
    else:
        result_desc += f"❌ Sayang sekali, kamu memilih {runners[user_choice_idx]['name']}. Kamu kehilangan **{taruhan}** koin."
        color = discord.Color.red()
        
    embed = discord.Embed(title="🏁 Hasil Balapan 🏁", description=result_desc, color=color)
    await msg.edit(embed=embed)

### GAME 8: COINFLIP
@game_group.command(name="coinflip", description="Lempar koin (Head/Tail). Peluang 50:50.")
@app_commands.describe(taruhan="Jumlah koin.", sisi="Pilih sisi koin.")
@app_commands.choices(sisi=[
    app_commands.Choice(name="🪙 Head (Gambar)", value="head"),
    app_commands.Choice(name="🦅 Tail (Angka)", value="tail")
])
async def coinflip(interaction: discord.Interaction, taruhan: app_commands.Range[int, 1], sisi: app_commands.Choice[str]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await send_auto_delete(interaction, "❌ Koinmu tidak cukup!", delay=5)
        return

    await bot.db.add_coins(interaction.user.id, -taruhan)
    await bot.db.record_game_play(interaction.user.id, "Coinflip")
    
    outcome = random.choice(["head", "tail"])
    outcome_name = "Head (Gambar) 🪙" if outcome == "head" else "Tail (Angka) 🦅"
    
    # Animasi suspense sederhana
    embed = discord.Embed(title="🪙 Melempar Koin...", description="Koin sedang berputar di udara...", color=discord.Color.gold())
    await interaction.response.send_message(embed=embed)
    await asyncio.sleep(2)
    
    if sisi.value == outcome:
        winnings = int(taruhan * 1.95) # 1.95x payout
        await bot.db.add_coins(interaction.user.id, winnings)
        embed = discord.Embed(title="🪙 Coinflip", description=f"Koin mendarat di: **{outcome_name}**\n🎉 Kamu Menang **{winnings}** koin!", color=discord.Color.green())
    else:
        embed = discord.Embed(title="🪙 Coinflip", description=f"Koin mendarat di: **{outcome_name}**\n❌ Kamu Kalah **{taruhan}** koin.", color=discord.Color.red())
        
    await interaction.edit_original_response(embed=embed)

@game_group.command(name="tictactoe", description="Main Tic-Tac-Toe (XOXO) melawan teman.")
@app_commands.describe(lawan="Pemain yang ingin kamu tantang.")
async def tictactoe(interaction: discord.Interaction, lawan: discord.User):
    if lawan.bot or lawan.id == interaction.user.id:
        await interaction.response.send_message("Kamu tidak bisa bermain melawan bot atau dirimu sendiri.", ephemeral=True)
        return

    view = TicTacToeView(interaction.user, lawan)
    embed = discord.Embed(title="🎮 Tic Tac Toe", description=f"{interaction.user.mention} (X) vs {lawan.mention} (O)\n\nGiliran {interaction.user.mention} (X)", color=discord.Color.blue())
    await interaction.response.send_message(embed=embed, view=view)

bot.tree.add_command(game_group)

# --- Perintah Admin ---
admin_group = app_commands.Group(
    name="admin", 
    description="Perintah khusus untuk pemilik bot.",
    # Hanya user dengan izin 'Administrator' yang bisa melihat command ini di daftar slash command.
    default_permissions=discord.Permissions(administrator=True),
    guild_only=True # Perintah admin sebaiknya hanya untuk server
)

@admin_group.command(name="givecoins", description="Berikan atau kurangi koin seorang pengguna.")
@app_commands.describe(user="Pengguna yang akan diubah saldonya.", amount="Jumlah koin (bisa negatif untuk mengurangi).")
@commands.is_owner() # Decorator ini menangani pengecekan kepemilikan bot
async def give_coins(interaction: discord.Interaction, user: discord.User, amount: int):
    # Pengecekan 'is_owner' sekarang ditangani secara otomatis oleh decorator.
    # Global error handler akan mengirim pesan jika pengguna non-owner mencoba.
    
    if user.bot:
        await send_auto_delete(interaction, "❌ Tidak bisa memberikan koin kepada bot.", delay=5)
        return

    user_data = await bot.db.get_user_data(user.id)
    new_balance = user_data['coins'] + amount
    
    await bot.db.update_user_balance(user.id, new_balance)
    
    embed = discord.Embed(description=f"✅ Berhasil mengubah saldo {user.mention} sebesar `{amount}` koin.\nSaldo barunya sekarang adalah **{new_balance}** koin.", color=discord.Color.green())
    await send_auto_delete(interaction, embed=embed, delay=5)

bot.tree.add_command(admin_group)

# --- Fitur Fun & Interaksi ---
fun_group = app_commands.Group(name="fun", description="Perintah seru-seruan dan interaksi sosial")

@fun_group.command(name="kerangajaib", description="Tanyakan apa saja pada Kerang Ajaib.")
async def kerang_ajaib(interaction: discord.Interaction, pertanyaan: str):
    jawaban = ["Ya.", "Tidak.", "Mungkin.", "Coba tanya lagi nanti.", "Tidak mungkin.", "Pasti.", "Saya rasa tidak.", "Tentu saja!", "Mimpi kamu terlalu tinggi.", "Bisa jadi."]
    embed = discord.Embed(title="🐚 Kerang Ajaib Bersabda", color=discord.Color.magenta())
    embed.add_field(name="Pertanyaan", value=pertanyaan, inline=False)
    embed.add_field(name="Jawaban", value=random.choice(jawaban), inline=False)
    await interaction.response.send_message(embed=embed)

@fun_group.command(name="rate", description="Beri nilai persentase untuk sesuatu atau seseorang.")
async def rate(interaction: discord.Interaction, target: str):
    rating = random.randint(0, 100)
    emoji = "🤮" if rating < 20 else "😐" if rating < 50 else "👍" if rating < 80 else "🔥"
    embed = discord.Embed(title="🤔 Rate Machine", description=f"Rating untuk **{target}**: **{rating}%** {emoji}", color=discord.Color.random())
    await interaction.response.send_message(embed=embed)

@fun_group.command(name="meme", description="Lihat meme acak dari internet.")
async def meme(interaction: discord.Interaction):
    await interaction.response.defer()
    # Menggunakan API publik meme-api.com
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://meme-api.com/gimme') as response:
                if response.status == 200:
                    data = await response.json()
                    embed = discord.Embed(title=data['title'], url=data['postLink'], color=discord.Color.random())
                    embed.set_image(url=data['url'])
                    embed.set_footer(text=f"👍 {data['ups']} | Subreddit: r/{data['subreddit']}")
                    await interaction.followup.send(embed=embed)
                else:
                    await interaction.followup.send("Gagal mengambil meme :( Coba lagi nanti.")
    except Exception as e:
        await interaction.followup.send(f"Terjadi kesalahan saat mengambil meme: {e}")

@fun_group.command(name="avatar", description="Lihat avatar pengguna secara full HD.")
async def avatar(interaction: discord.Interaction, user: discord.User = None):
    target = user or interaction.user
    embed = discord.Embed(title=f"Avatar {target.display_name}", color=target.color)
    embed.set_image(url=target.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@fun_group.command(name="gombal", description="Dapatkan gombalan maut acak.")
async def gombal(interaction: discord.Interaction, user: discord.User = None):
    gombalan_list = [
        "Kamu itu kayak lempeng bumi, geser dikit aja gempa di hatiku.",
        "Bapak kamu maling ya? Soalnya kamu pintar mencuri hatiku.",
        "Cuka apa yang manis? Cuka sama kamu.",
        "Kamu tau gak bedanya kamu sama jam 12? Jam 12 kesiangan, kalau kamu kesayangan.",
        "Jepang bikin robot, Jerman bikin mobil, kamu bikin kangen.",
        "Kalau kamu jadi senar gitar, aku nggak mau jadi gitarisnya. Aku nggak mau mutusin kamu.",
        "Tahu gak kenapa menara pisa miring? Soalnya ketarik sama senyummu.",
        "Napas aku kok sesek banget ya? Oh, ternyata separuh nafasku ada di kamu.",
        "Kamu tau gak kenapa aku suka minum kopi? Soalnya kopi itu pahit, yang manis itu cuma kamu.",
        "Kalau disuruh milih antara napas atau mencintaimu, aku bakal gunain napas terakhirku buat bilang aku cinta kamu.",
        "Kamu punya peta gak? Aku tersesat di matamu.",
        "Aku rela jadi abang nasi goreng, asalkan setiap malam aku bisa lewat di depan rumahmu.",
        "Cintaku padamu itu kayak utang, awalnya kecil, lama-lama gede sendiri.",
        "Kamu tau gak bedanya kamu sama monas? Monas milik pemerintah, kalau kamu milik aku.",
        "Sejak kenal kamu, aku jadi susah tidur. Soalnya kenyataan lebih indah daripada mimpi.",
        "Kalau kamu jadi bunga, aku rela jadi kumbangnya. Biar bisa selalu dekat sama kamu.",
        "Kamu itu kayak wifi, sinyalnya kuat banget nyambung ke hatiku.",
        "Aku gak sedih besok hari senin, aku sedihnya kalau besok gak ketemu kamu.",
        "Tau gak kenapa pelangi cuma setengah lingkaran? Soalnya setengahnya lagi ada di matamu.",
        "Kamu tau gak persamaan kamu sama soal ujian? Sama-sama perlu diperjuangkan."
    ]
    gombalan = random.choice(gombalan_list)
    target = f" untuk {user.mention}" if user else ""
    embed = discord.Embed(title="😏 Gombalan Maut", description=f"{gombalan}", color=discord.Color.pink())
    if user:
        embed.set_footer(text=f"Spesial untuk {user.display_name}")
    await interaction.response.send_message(content=user.mention if user else None, embed=embed)

@fun_group.command(name="ship", description="Cek kecocokan cinta antara dua orang.")
async def ship(interaction: discord.Interaction, user1: discord.User, user2: discord.User = None):
    if user2 is None:
        user2 = interaction.user
        
    # Gunakan seed dari ID user agar hasilnya konsisten setiap hari (opsional, hapus seed jika ingin full random)
    # random.seed(user1.id + user2.id + datetime.now().day) 
    score = random.randint(0, 100)
    # random.seed() # Reset seed
    
    if user1.id == user2.id:
        score = 101 # Self love
        
    bar = '█' * (score // 10) + '░' * (10 - (score // 10))
    
    desc = f"💗 **{user1.display_name}** x **{user2.display_name}** 💗\n\n"
    desc += f"**{score}%** `{bar}`\n\n"
    
    if score > 90:
        desc += "🔥 Pasangan Sempurna! Segera nikah!"
    elif score > 70:
        desc += "🥰 Cocok banget!"
    elif score > 40:
        desc += "🤔 Boleh lah dicoba."
    else:
        desc += "💔 Cari yang lain aja..."
        
    embed = discord.Embed(title="💘 Love Calculator", description=desc, color=discord.Color.pink())
    await interaction.response.send_message(embed=embed)

@fun_group.command(name="pilih", description="Biarkan bot memilihkan sesuatu untukmu.")
@app_commands.describe(pilihan="Pilihan dipisahkan dengan koma (contoh: Nasi Goreng, Mie Ayam, Bakso)")
async def pilih(interaction: discord.Interaction, pilihan: str):
    options = [x.strip() for x in pilihan.split(',')]
    if len(options) < 2:
        await interaction.response.send_message("Berikan minimal 2 pilihan dipisahkan koma. Contoh: `Nasi Goreng, Mie Ayam`", ephemeral=True)
        return
        
    chosen = random.choice(options)
    embed = discord.Embed(title="🤔 Saya Memilih...", description=f"**{chosen}**!", color=discord.Color.random())
    await interaction.response.send_message(embed=embed)

@fun_group.command(name="interaksi", description="Lakukan interaksi dengan pengguna lain (peluk, tampar, dll).")
@app_commands.choices(aksi=[
    app_commands.Choice(name="Peluk (Hug) 🤗", value="hug"),
    app_commands.Choice(name="Tampar (Slap) 👋", value="slap"),
    app_commands.Choice(name="Elus (Pat) 💆", value="pat"),
    app_commands.Choice(name="Tonjok (Punch) 👊", value="punch"),
    app_commands.Choice(name="Cium (Kiss) 💋", value="kiss"),
    app_commands.Choice(name="Tos (Highfive) 🙌", value="highfive"),
    app_commands.Choice(name="Bunuh (Kill) 🔪", value="kill"),
    app_commands.Choice(name="Colek (Poke) 👉", value="poke")
])
async def interaksi(interaction: discord.Interaction, aksi: app_commands.Choice[str], user: discord.User):
    if user == interaction.user:
        embed = discord.Embed(
            title="⚠️ Ups!", 
            description="Kamu tidak bisa melakukan itu ke dirimu sendiri!\n*(Kecuali kamu sangat kesepian...)*", 
            color=discord.Color.orange()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    gifs = {
        "hug": [
            "https://media.giphy.com/media/3bqtLDeiDtwhq/giphy.gif",
            "https://media.giphy.com/media/l2QDM9Jnim1YVILXa/giphy.gif",
            "https://media.giphy.com/media/od5H3PmEG5EVq/giphy.gif",
            "https://media.giphy.com/media/u9BxQbM5bxvwY/giphy.gif",
            "https://media.giphy.com/media/PHZ7v9tfQu0o0/giphy.gif",
            "https://media.giphy.com/media/49mdjsMrH7oze/giphy.gif",
            "https://media.giphy.com/media/lrr9rHuoJOE0w/giphy.gif"
        ],
        "slap": [
            "https://media.giphy.com/media/X3Yj4XXXieKYM/giphy.gif",
            "https://media.giphy.com/media/mEtSQlxqBtWWA/giphy.gif",
            "https://media.giphy.com/media/Gf3AUz3eBNbTW/giphy.gif",
            "https://media.giphy.com/media/10Am8idu3qMO9q/giphy.gif",
            "https://media.giphy.com/media/Zau0yrl17uzdK/giphy.gif",
            "https://media.giphy.com/media/jLeyZWgtwgr2U/giphy.gif",
            "https://media.giphy.com/media/6Fad0loHc6Cbe/giphy.gif"
        ],
        "pat": [
            "https://media.giphy.com/media/5tmRHwTlHAA9WkVxTU/giphy.gif",
            "https://media.giphy.com/media/L2z7dnOduqE6Y/giphy.gif",
            "https://media.giphy.com/media/4HP0ddZnNVvKU/giphy.gif",
            "https://media.giphy.com/media/109ltuoSQ9owmY/giphy.gif",
            "https://media.giphy.com/media/ye7OTQgwmVuVy/giphy.gif",
            "https://media.giphy.com/media/ARSp9T7wwxNcs/giphy.gif",
            "https://media.giphy.com/media/osYdfUptPqV0s/giphy.gif"
        ],
        "punch": [
            "https://media1.tenor.com/m/BoYBoopIkBcAAAAC/anime-punch.gif",
            "https://media1.tenor.com/m/p_mI1TqG38EAAAAC/anime-punch.gif",
            "https://media1.tenor.com/m/tL3_uBflIuAAAAAC/saitama-punch.gif",
            "https://media1.tenor.com/m/6a42QlkVsXEAAAAC/anime-punch.gif",
            "https://media1.tenor.com/m/EvZ809qZCdEAAAAC/anime-punch.gif",
            "https://media1.tenor.com/m/Swy_yI2gK9AAAAAC/anime-punch.gif",
            "https://media1.tenor.com/m/uh8j85_6O_kAAAAC/anime-punch.gif"
        ],
        "kiss": [
            "https://media1.tenor.com/m/7T1cOybcvSgAAAAC/anime-kiss.gif",
            "https://media1.tenor.com/m/F02Ep3b2jJgAAAAC/cute-anime-kiss.gif",
            "https://media1.tenor.com/m/v4Ur0OCvaXcAAAAC/anime-kiss.gif",
            "https://media1.tenor.com/m/9351312684156726296/kiss-anime.gif"
        ],
        "highfive": [
            "https://media1.tenor.com/m/JBBZ9mQkYV0AAAAC/high-five-anime.gif",
            "https://media1.tenor.com/m/7h8yI-2XhzoAAAAC/high-five.gif",
            "https://media1.tenor.com/m/291666992226425574/anime-high-five.gif"
        ],
        "kill": [
            "https://media1.tenor.com/m/G4sG61m_9kAAAAAC/anime-kill.gif",
            "https://media1.tenor.com/m/1w8J3_4X_xUAAAAC/akame-ga-kill.gif",
            "https://media1.tenor.com/m/9351312684156726296/kill-anime.gif" 
        ],
        "poke": [
            "https://media1.tenor.com/m/y4R6FhrWlswAAAAC/anime-poke.gif",
            "https://media1.tenor.com/m/3xI1b3_p-9kAAAAC/poke-anime.gif",
            "https://media1.tenor.com/m/9351312684156726296/poke-anime.gif"
        ]
    }
    
    # Konfigurasi tampilan untuk setiap aksi (Judul, Warna, Teks)
    action_config = {
        "hug": {"title": "🤗 Pelukan Hangat!", "color": discord.Color.from_rgb(255, 182, 193), "text": f"memeluk {user.mention} dengan erat!"},
        "slap": {"title": "👋 Tamparan Keras!", "color": discord.Color.red(), "text": f"menampar {user.mention}! Aduh sakit..."},
        "pat": {"title": "💆 Elus-elus", "color": discord.Color.gold(), "text": f"mengelus kepala {user.mention}. Good boy/girl!"},
        "punch": {"title": "👊 Tonjokan Maut!", "color": discord.Color.dark_red(), "text": f"menonjok {user.mention}!"},
        "kiss": {"title": "💋 Muah!", "color": discord.Color.magenta(), "text": f"mencium {user.mention}!"},
        "highfive": {"title": "🙌 High Five!", "color": discord.Color.orange(), "text": f"mengajak {user.mention} tos!"},
        "kill": {"title": "🔪 Wasted", "color": discord.Color.dark_red(), "text": f"membunuh {user.mention}!"},
        "poke": {"title": "👉 Colek", "color": discord.Color.teal(), "text": f"mencolek {user.mention}."}
    }

    config = action_config.get(aksi.value)
    gif_url = random.choice(gifs.get(aksi.value, []))
    
    embed = discord.Embed(
        title=config["title"], 
        description=f"{interaction.user.mention} {config['text']}", 
        color=config["color"]
    )
    embed.set_image(url=gif_url)
    embed.set_footer(text=f"Dikirim oleh {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
    
    await interaction.response.send_message(content=user.mention, embed=embed)

bot.tree.add_command(fun_group)

# --- Fitur Sosial (Profil & Reputasi) ---

@bot.tree.command(name="profile", description="Lihat profil sosial Anda atau pengguna lain.")
@app_commands.describe(user="Pengguna yang profilnya ingin Anda lihat (opsional).")
async def profile(interaction: discord.Interaction, user: discord.User = None):
    target_user = user or interaction.user

    if target_user.bot:
        await send_auto_delete(interaction, "🤖 Bot tidak memiliki profil!", delay=3)
        return

    user_data = await bot.db.get_user_data(target_user.id)

    # Data dari database dengan nilai default
    level = user_data.get('level', 1)
    xp = user_data.get('xp', 0)
    coins = user_data.get('coins', 0)
    reputation = user_data.get('reputation', 0)
    birthday_str_db = user_data.get('birthday')

    # Kalkulasi XP untuk level berikutnya
    xp_needed = level * 100
    
    # Membuat progress bar
    progress = int((xp / xp_needed) * 20) if xp_needed > 0 else 0
    bar = '█' * progress + '░' * (20 - progress)

    # Format tanggal ulang tahun
    display_birthday = "Belum diatur"
    if birthday_str_db:
        try:
            birth_date = datetime.strptime(birthday_str_db, "%m-%d")
            display_birthday = birth_date.strftime("%d %B") # e.g., 25 Desember
        except (ValueError, TypeError):
            display_birthday = "Format salah"

    embed = discord.Embed(
        title=f" Profil Pengguna",
        description=f"Data statistik untuk {target_user.mention}",
        color=target_user.accent_color or discord.Color.blurple()
    )
    embed.set_thumbnail(url=target_user.display_avatar.url)
    
    embed.add_field(name="🏆 Level", value=f"```{level}```", inline=True)
    embed.add_field(name="✨ Reputasi", value=f"```{reputation}```", inline=True)
    embed.add_field(name="💰 Saldo", value=f"```{coins:,}```", inline=True)
    
    embed.add_field(name=f"📊 Progress XP ({xp}/{xp_needed})", value=f"`{bar}`", inline=False)
    embed.add_field(name="🎂 Ulang Tahun", value=f"🗓️ {display_birthday}", inline=False)

    # --- Statistik Game ---
    game_stats = await bot.db.get_game_stats(target_user.id)
    
    if game_stats:
        # Game Favorit (Top 3)
        fav_text = ""
        for stat in game_stats[:3]:
            fav_text += f"{stat['game_name']:<14} — {stat['total_plays']}x main\n"
        embed.add_field(name="🎮 Game Favorit", value=f"```{fav_text}```", inline=False)
        
        # Game Paling Aktif (Minggu Ini)
        weekly_active = [s for s in game_stats if s['weekly_plays'] > 0]
        if weekly_active:
            best_weekly = max(weekly_active, key=lambda x: x['weekly_plays'])
            embed.add_field(name="🔥 Game Paling Aktif", value=f"```{best_weekly['game_name']} ({best_weekly['weekly_plays']}x minggu ini)```", inline=False)

    embed.set_footer(text=f"ID Pengguna: {target_user.id}")

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rep", description="Berikan satu poin reputasi kepada pengguna lain (cooldown 24 jam).")
@app_commands.describe(user="Pengguna yang ingin Anda beri reputasi.")
async def rep(interaction: discord.Interaction, user: discord.User):
    giver = interaction.user
    receiver = user

    if giver.id == receiver.id:
        await send_auto_delete(interaction, "❌ Anda tidak bisa memberikan reputasi untuk diri sendiri!", delay=5)
        return
    if receiver.bot:
        await send_auto_delete(interaction, "❌ Anda tidak bisa memberikan reputasi kepada bot!", delay=5)
        return

    giver_data = await bot.db.get_user_data(giver.id)
    now = datetime.now(timezone.utc)
    cooldown = timedelta(hours=24)
    last_rep_time = giver_data.get('last_rep_time')

    if last_rep_time and (now - last_rep_time) < cooldown:
        time_left = cooldown - (now - last_rep_time)
        hours, remainder = divmod(int(time_left.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        await send_auto_delete(interaction, f"⏳ Anda harus menunggu **{hours} jam {minutes} menit** lagi untuk bisa memberikan reputasi.", delay=5)
        return

    await bot.db.give_reputation(giver_id=giver.id, receiver_id=receiver.id)
    embed = discord.Embed(description=f"✅ Anda telah memberikan 1 poin reputasi kepada {receiver.mention}!", color=discord.Color.green())
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Lihat papan peringkat server.")
@app_commands.describe(
    kategori="Pilih kategori papan peringkat yang ingin dilihat.",
    scope="Pilih lingkup leaderboard: Global (semua server) atau Server (server ini saja)."
)
@app_commands.choices(kategori=[
    app_commands.Choice(name="Koin Terbanyak", value="coins"),
    app_commands.Choice(name="Level Tertinggi", value="level"),
    app_commands.Choice(name="Reputasi Teratas", value="reputation"),
], scope=[
    app_commands.Choice(name="Global 🌍", value="global"),
    app_commands.Choice(name="Server 🏠", value="server")
])
async def leaderboard(interaction: discord.Interaction, kategori: app_commands.Choice[str], scope: app_commands.Choice[str] = None):
    await interaction.response.defer(ephemeral=False) # Menunda respons karena query DB bisa lama

    # Default scope ke global jika tidak dipilih
    scope_value = scope.value if scope else "global"
    
    user_ids_filter = None
    if scope_value == "server":
        if not interaction.guild:
            await interaction.followup.send("❌ Leaderboard server hanya bisa digunakan di dalam server.")
            return
        # Ambil list ID member di server ini (exclude bot)
        user_ids_filter = [member.id for member in interaction.guild.members if not member.bot]

    leaderboard_data = await bot.db.get_leaderboard(sort_by=kategori.value, limit=10, user_ids=user_ids_filter)

    if not leaderboard_data:
        await interaction.followup.send("Belum ada data untuk ditampilkan di papan peringkat.")
        return

    title_map = {
        "coins": "💰 Papan Peringkat Koin",
        "level": "🏆 Papan Peringkat Level",
        "reputation": "✨ Papan Peringkat Reputasi"
    }
    
    unit_map = {
        "coins": "koin",
        "level": "Level",
        "reputation": "rep"
    }

    scope_title = "Global 🌍" if scope_value == "global" else f"Server {interaction.guild.name} 🏠"

    embed = discord.Embed(
        title=f"{title_map.get(kategori.value, 'Papan Peringkat')} - {scope_title}",
        description="Berikut adalah Top 10 pengguna teratas:",
        color=discord.Color.gold()
    )

    description = ""
    for i, record in enumerate(leaderboard_data):
        user_id = record['user_id']
        value = record[kategori.value]
        
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        user_name = user.display_name if user else f"User (ID: {user_id})"
        
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"**{i+1}.**"
        description += f"{emoji} {user.mention} - **{value}** {unit_map.get(kategori.value)}\n"

    embed.description = description
    embed.set_footer(text=f"Top 10 {scope_title} berdasarkan {kategori.name}")

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="pay", description="Transfer koin ke pengguna lain.")
@app_commands.describe(
    user="Pengguna yang akan menerima koin.",
    amount="Jumlah koin yang ingin ditransfer (minimal 1)."
)
async def pay(interaction: discord.Interaction, user: discord.User, amount: app_commands.Range[int, 1]):
    giver = interaction.user
    receiver = user

    # --- Validasi ---
    if giver.id == receiver.id:
        await send_auto_delete(interaction, "❌ Kamu tidak bisa mentransfer koin ke dirimu sendiri!", delay=5)
        return
    
    if receiver.bot:
        await send_auto_delete(interaction, "❌ Kamu tidak bisa mentransfer koin ke bot!", delay=5)
        return

    giver_data = await bot.db.get_user_data(giver.id)
    
    if giver_data['coins'] < amount:
        await send_auto_delete(interaction, f"❌ Koinmu tidak cukup! Kamu hanya punya {giver_data['coins']} koin.", delay=5)
        return

    # --- Proses Transfer (BUG FIX: Gunakan add_coins) ---
    await bot.db.add_coins(giver.id, -amount)
    await bot.db.add_coins(receiver.id, amount)

    # --- Konfirmasi ---
    embed = discord.Embed(title="💸 Transfer Berhasil", description=f"Kamu berhasil mentransfer **{amount}** koin kepada {receiver.mention}.", color=discord.Color.green())
    await interaction.response.send_message(embed=embed)

if __name__ ==  "__main__":
    if TOKEN and DATABASE_URL:

        print(f"🐍 Python: {sys.version.split()[0]}")
        print(f"🤖 Discord.py: {discord.__version__}")
        print("🔄 Memulai sistem...", flush=True)
        try:
            keep_alive()
            print("✅ Web Server berjalan (Cek keep_alive.py untuk info port).", flush=True)
            print("🚀 Sedang login ke Discord...", flush=True)
            bot.run(TOKEN)
        except discord.errors.PrivilegedIntentsRequired:
             logging.critical("❌ ERROR INTENTS: Mohon aktifkan 'Message Content Intent' dan 'Server Members Intent' di Discord Developer Portal.")
        except discord.errors.LoginFailure:
             logging.critical("❌ ERROR TOKEN: Token bot tidak valid. Cek file .env.")
        except Exception as e:
            logging.critical(f"❌ BOT CRASH: {e}")
    else:
        print("Error: Pastikan DISCORD_TOKEN dan DATABASE_URL sudah diatur di file .env")