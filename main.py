import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from dotenv import load_dotenv
import asyncio
import random
from datetime import datetime, timedelta, timezone, time

# Impor kelas DatabaseManager yang kita buat
from database import DatabaseManager

# --- Konfigurasi & Variabel Global ---
load_dotenv()
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
RPS_REWARD = 75

# Konfigurasi Leveling
XP_PER_MESSAGE_MIN = 15
XP_PER_MESSAGE_MAX = 25
XP_COOLDOWN_SECONDS = 60

KATA_LIST = ["python", "discord", "program", "komputer", "internet", "server", "jaringan", "database", "algoritma", "variabel", "proyek", "kode"]

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
        # Inisialisasi DatabaseManager
        self.db = DatabaseManager(dsn=DATABASE_URL)
        # Cooldown untuk on_message agar tidak membebani DB
        self.xp_cooldowns = {}

    async def setup_hook(self):
        # Hubungkan ke DB dan siapkan tabel sebelum bot siap
        await self.db.connect()
        await self.db.init_db()

        # Mulai background task
        self.birthday_checker.start()
        
        # Selama development, lebih baik sync per server menggunakan !sync.
        # Baris di bawah ini bisa diaktifkan kembali jika bot sudah final.
        # await self.tree.sync()
    
    async def on_ready(self):
        print(f'{self.user} telah online dan siap digunakan')
        print('------')
    
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
    
    # PART 5: Listener untuk memberikan XP saat user mengirim pesan
    async def on_message(self, message: discord.Message):
        # Abaikan pesan dari bot atau yang bukan dari server
        if message.author.bot or not message.guild:
            return

        # Proses command prefix jika ada
        await self.process_commands(message)

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
            
            await message.channel.send(f"🎉 Selamat, {message.author.mention}! Kamu telah mencapai **Level {new_level}**!")

bot = MyBot()

# --- Command Tambahan untuk Developer ---
# Ketik '!sync' di chat untuk memunculkan command baru secara instan
@bot.command()
@commands.is_owner()
async def sync(ctx):
    # Salin semua command global ke server ini
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    await ctx.send(f"✅ Berhasil sinkronisasi {len(synced)} command ke server ini! Coba ketik / sekarang.")

@bot.command()
@commands.is_owner()
async def unsync(ctx):
    # Menghapus command khusus dari server ini
    bot.tree.clear_commands(guild=ctx.guild)
    await bot.tree.sync(guild=ctx.guild)
    await ctx.send("✅ Berhasil menghapus command dari server ini. Gunakan `!sync` untuk menambahkannya kembali.")

@bot.tree.command(name="ping", description="Mengecek latensi bot")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000) 

    await interaction.response.send_message(f"Pong! 🏓 Latency: {latency}ms")

# Command untuk mengecek saldo
@bot.tree.command(name="balance", description="Cek saldo koin Anda.")
async def balance(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    # Ambil data dari database
    user_data = await bot.db.get_user_data(user_id)
    
    embed = discord.Embed(
        title=f"💰 Saldo Koin {interaction.user.display_name}",
        description=f"Anda saat ini memiliki **{user_data['coins']}** koin.",
        color=discord.Color.gold()
    )
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
            
            await interaction.response.send_message(f"Anda harus menunggu **{hours} jam {minutes} menit** lagi untuk bisa klaim hadiah harian.", ephemeral=True)
            return

    # Logika jika cooldown sudah selesai atau klaim pertama kali
    reward = random.randint(100, 500)
    new_balance = user_data['coins'] + reward
    
    # Update database
    await bot.db.update_user_balance(user_id, new_balance, now)
    
    await interaction.response.send_message(f"🎉 Anda berhasil mengklaim hadiah harian sebesar **{reward}** koin! Saldo Anda sekarang adalah **{new_balance}** koin.")

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
        
        await interaction.response.send_message(f"✅ Ulang tahunmu berhasil diatur ke tanggal **{tanggal}**!", ephemeral=True)

    except ValueError:
        await interaction.response.send_message("❌ Format tanggal salah! Harap gunakan format **DD-MM**, contoh: `25-12`.", ephemeral=True)

@birthday_group.command(name="info", description="Lihat tanggal ulang tahun yang tersimpan")
async def info_birthday(interaction: discord.Interaction):
    user_data = await bot.db.get_user_data(interaction.user.id)
    birthday_str_db = user_data.get('birthday')

    if birthday_str_db:
        # Konversi dari MM-DD (DB) ke DD-MM (Display)
        birth_date = datetime.strptime(birthday_str_db, "%m-%d")
        display_date = birth_date.strftime("%d-%m")
        await interaction.response.send_message(f"ℹ️ Tanggal ulang tahunmu tercatat pada: **{display_date}**.", ephemeral=True)
    else:
        await interaction.response.send_message("Kamu belum mengatur tanggal ulang tahunmu. Gunakan `/birthday set`.", ephemeral=True)

# Daftarkan command group ke bot
bot.tree.add_command(birthday_group)

# --- Fitur Game ---

@bot.tree.command(name="tebakkata", description="Main tebak kata dari huruf yang diacak.")
async def tebak_kata(interaction: discord.Interaction):
    kata_asli = random.choice(KATA_LIST)
    huruf_acak = ''.join(random.sample(kata_asli, len(kata_asli)))

    embed = discord.Embed(
        title=" unscramble 🔡 Tebak Kata!",
        description=f"Aku punya kata: **`{huruf_acak.upper()}`**\n\nCoba tebak kata aslinya apa? Kamu punya waktu 15 detik!",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed)

    def check(m):
        # Hanya cek pesan dari user yang menjalankan command di channel yang sama
        return m.author == interaction.user and m.channel == interaction.channel

    try:
        msg = await bot.wait_for('message', check=check, timeout=15.0)
        
        if msg.content.lower() == kata_asli:
            user_data = await bot.db.get_user_data(interaction.user.id)
            new_balance = user_data['coins'] + TEBAK_KATA_REWARD
            await bot.db.update_user_balance(interaction.user.id, new_balance)
            await interaction.followup.send(f"🎉 Benar sekali! Jawabannya adalah **{kata_asli}**. Kamu mendapatkan **{TEBAK_KATA_REWARD}** koin!")
        else:
            await interaction.followup.send(f"❌ Salah! Jawaban yang benar adalah **{kata_asli}**. Coba lagi lain kali!")

    except asyncio.TimeoutError:
        await interaction.followup.send(f"⏰ Waktu habis! Jawaban yang benar adalah **{kata_asli}**.")

@bot.tree.command(name="mathbattle", description="Selesaikan soal matematika dalam 10 detik!")
async def math_battle(interaction: discord.Interaction):
    ops = ['+', '-']
    op = random.choice(ops)
    num1 = random.randint(10, 99)
    num2 = random.randint(1, num1 if op == '-' else 99) # Pastikan hasil tidak negatif

    if op == '+':
        jawaban = num1 + num2
    else:
        jawaban = num1 - num2

    await interaction.response.send_message(f"⚔️ **Math Battle!** Berapa hasil dari **`{num1} {op} {num2}`**? Waktumu 10 detik!")

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel

    try:
        msg = await bot.wait_for('message', check=check, timeout=10.0)
        
        if int(msg.content) == jawaban:
            user_data = await bot.db.get_user_data(interaction.user.id)
            new_balance = user_data['coins'] + MATH_BATTLE_REWARD
            await bot.db.update_user_balance(interaction.user.id, new_balance)
            await interaction.followup.send(f"🧠 Cerdas! Jawabannya **{jawaban}**. Kamu dapat **{MATH_BATTLE_REWARD}** koin!")
        else:
            await interaction.followup.send(f"❌ Salah! Jawaban yang benar adalah **{jawaban}**.")
    
    except asyncio.TimeoutError:
        await interaction.followup.send(f"⏰ Waktu habis! Jawabannya adalah **{jawaban}**.")
    except (ValueError, TypeError):
        await interaction.followup.send(f"❌ Itu bukan angka! Jawaban yang benar adalah **{jawaban}**.")

@bot.tree.command(name="higherlower", description="Tebak angka rahasia antara 1-100.")
async def higher_lower(interaction: discord.Interaction):
    angka_rahasia = random.randint(1, 100)
    kesempatan = 5

    await interaction.response.send_message(f"🤔 Aku telah memilih angka antara 1 dan 100. Kamu punya **{kesempatan}** kesempatan untuk menebaknya!")

    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel

    for i in range(kesempatan):
        try:
            msg = await bot.wait_for('message', check=check, timeout=50.0)
            tebakan = int(msg.content)

            if tebakan == angka_rahasia:
                user_data = await bot.db.get_user_data(interaction.user.id)
                new_balance = user_data['coins'] + HIGHER_LOWER_REWARD
                await bot.db.update_user_balance(interaction.user.id, new_balance)
                await interaction.followup.send(f"🏆 **HEBAT!** Kamu berhasil menebak angkanya, yaitu **{angka_rahasia}**! Kamu memenangkan **{HIGHER_LOWER_REWARD}** koin!")
                return # Keluar dari fungsi jika sudah menang
            
            elif tebakan < angka_rahasia:
                sisa_kesempatan = kesempatan - (i + 1)
                await interaction.followup.send(f"🔼 **Lebih Tinggi!** (Sisa kesempatan: {sisa_kesempatan})")
            else:
                sisa_kesempatan = kesempatan - (i + 1)
                await interaction.followup.send(f"🔽 **Lebih Rendah!** (Sisa kesempatan: {sisa_kesempatan})")

        except asyncio.TimeoutError:
            await interaction.followup.send(f"⏰ Waktu menebak habis! Angka rahasianya adalah **{angka_rahasia}**.")
            return
        except (ValueError, TypeError):
            await interaction.followup.send("Itu bukan angka yang valid. Coba lagi.")

    # Jika loop selesai tanpa menang
    await interaction.followup.send(f"GAME OVER! Kamu kehabisan kesempatan. Angka rahasianya adalah **{angka_rahasia}**.")

# --- Game Baru: Batu Kertas Gunting ---
class RPSView(discord.ui.View):
    def __init__(self, author):
        super().__init__(timeout=30)
        self.author = author

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Ini bukan permainanmu!", ephemeral=True)
            return False
        return True

    async def handle_choice(self, interaction: discord.Interaction, user_choice: str):
        bot_choice = random.choice(["batu", "kertas", "gunting"])
        
        # Disable semua tombol setelah dipilih
        for item in self.children:
            item.disabled = True
        
        result_message = ""
        win = False

        if user_choice == bot_choice:
            result_message = f"⚖️ Hasilnya **Seri**! Kalian berdua memilih **{user_choice}**."
        elif (user_choice == "batu" and bot_choice == "gunting") or \
             (user_choice == "kertas" and bot_choice == "batu") or \
             (user_choice == "gunting" and bot_choice == "kertas"):
            result_message = f"🎉 Kamu **Menang**! Kamu memilih **{user_choice}** dan bot memilih **{bot_choice}**."
            win = True
        else:
            result_message = f"😭 Kamu **Kalah**! Kamu memilih **{user_choice}** dan bot memilih **{bot_choice}**."

        if win:
            user_data = await bot.db.get_user_data(interaction.user.id)
            new_balance = user_data['coins'] + RPS_REWARD
            await bot.db.update_user_balance(interaction.user.id, new_balance)
            result_message += f"\nKamu mendapatkan **{RPS_REWARD}** koin!"

        await interaction.response.edit_message(content=result_message, view=self)
        self.stop()

    @discord.ui.button(label="Batu 🗿", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "batu")

    @discord.ui.button(label="Kertas 📄", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "kertas")

    @discord.ui.button(label="Gunting ✂️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_choice(interaction, "gunting")

@bot.tree.command(name="rps", description="Main Batu Kertas Gunting melawan bot.")
async def rps(interaction: discord.Interaction):
    view = RPSView(interaction.user)
    await interaction.response.send_message("Pilih gerakanmu!", view=view)

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
            await interaction.response.send_message("Ini bukan permainanmu!", ephemeral=True)
            return False
        return True

    def create_embed(self, status_message: str, is_game_over: bool = False) -> discord.Embed:
        """Membuat dan memformat embed untuk game."""
        tower_visual = ""
        for i in range(self.max_level, 0, -1):
            if i == self.level:
                tower_visual += f"Lantai {i}: 🧗\n"
            elif i < self.level:
                tower_visual += f"Lantai {i}: 🟩\n"
            else:
                tower_visual += f"Lantai {i}: ⬜\n"
        tower_visual += "Dasar: 🏃"

        color = discord.Color.red() if "kalah" in status_message.lower() else (discord.Color.green() if is_game_over else discord.Color.blue())
        embed = discord.Embed(title="🗼 Risk Tower", description=status_message, color=color)
        embed.add_field(name="Visual Menara", value=f"```\n{tower_visual}\n```", inline=False)
        embed.add_field(name="Taruhan Awal", value=f"{self.bet} koin")
        embed.add_field(name="Lantai Saat Ini", value=self.level)
        embed.add_field(name="Hadiah Jika Cash Out", value=f"**{self.current_reward} koin**")
        embed.set_footer(text=f"Bermain sebagai: {self.author.display_name}")
        return embed

    async def end_game(self, interaction: discord.Interaction, message: str):
        """Menonaktifkan tombol dan mengakhiri game."""
        for item in self.children:
            item.disabled = True
        embed = self.create_embed(message, is_game_over=True)
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
                user_data = await bot.db.get_user_data(self.author.id)
                await bot.db.update_user_balance(self.author.id, user_data['coins'] + self.current_reward)
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
            user_data = await bot.db.get_user_data(self.author.id)
            await bot.db.update_user_balance(self.author.id, user_data['coins'] + self.current_reward)
            await self.end_game(interaction, f"✅ Aman! Kamu berhasil cash out dan mendapatkan **{self.current_reward}** koin.")
        else:
            await self.end_game(interaction, "Kamu turun tanpa membawa apa-apa.")

@game_group.command(name="risktower", description="Daki menara untuk hadiah besar, tapi hati-hati jangan sampai jatuh!")
@app_commands.describe(taruhan="Jumlah koin yang ingin dipertaruhkan.")
async def risk_tower(interaction: discord.Interaction, taruhan: app_commands.Range[int, 1]):
    user_data = await bot.db.get_user_data(interaction.user.id)
    if user_data['coins'] < taruhan:
        await interaction.response.send_message("❌ Koinmu tidak cukup untuk taruhan ini!", ephemeral=True)
        return

    # Langsung potong taruhan
    await bot.db.update_user_balance(interaction.user.id, user_data['coins'] - taruhan)

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
            await interaction.response.send_message("Ini bukan permainanmu!", ephemeral=True)
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
            user_data = await bot.db.get_user_data(self.author.id)
            await bot.db.update_user_balance(self.author.id, user_data['coins'] + reward)
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
            user_data = await bot.db.get_user_data(self.author.id)
            await bot.db.update_user_balance(self.author.id, user_data['coins'] + reward)
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
        await interaction.response.send_message("❌ Koinmu tidak cukup untuk taruhan ini!", ephemeral=True)
        return

    await bot.db.update_user_balance(interaction.user.id, user_data['coins'] - taruhan)
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
            await interaction.response.send_message("Ini bukan permainanmu!", ephemeral=True)
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
            await interaction.edit_original_response(content=f"🔮 Sosok itu membuka kartumu... **ZONK**! Kamu kehilangan **{self.bet}** koin.")
        else:
            user_data = await bot.db.get_user_data(self.author.id)
            await bot.db.update_user_balance(self.author.id, user_data['coins'] + reward)
            await interaction.edit_original_response(content=f"🔮 Sosok itu membuka kartumu... **JACKPOT**! Kamu memenangkan **{reward}** koin!")
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
        await interaction.response.send_message("❌ Koinmu tidak cukup untuk taruhan ini!", ephemeral=True)
        return

    await bot.db.update_user_balance(interaction.user.id, user_data['coins'] - taruhan)
    view = ShadowDealView(interaction.user, taruhan)
    embed = discord.Embed(title="🎭 Shadow Deal", description=f"Sosok misterius muncul dari bayangan. Dia menawarimu sebuah permainan.\n\n\"Pilih satu dari tiga kartu ini,\" bisiknya. \"Nasibmu ada di tanganmu.\"\n\nKamu mempertaruhkan **{taruhan}** koin.", color=discord.Color.purple())
    await interaction.response.send_message(embed=embed, view=view)

bot.tree.add_command(game_group)

# --- Fitur Sosial (Profil & Reputasi) ---

@bot.tree.command(name="profile", description="Lihat profil sosial Anda atau pengguna lain.")
@app_commands.describe(user="Pengguna yang profilnya ingin Anda lihat (opsional).")
async def profile(interaction: discord.Interaction, user: discord.User = None):
    target_user = user or interaction.user

    if target_user.bot:
        await interaction.response.send_message("🤖 Bot tidak memiliki profil!", ephemeral=True)
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
        title=f"📜 Profil {target_user.display_name}",
        color=target_user.accent_color or discord.Color.blurple()
    )
    embed.set_thumbnail(url=target_user.display_avatar.url)
    
    embed.add_field(name="🏆 Level", value=f"**{level}**", inline=True)
    embed.add_field(name="✨ Reputasi", value=f"**{reputation}**", inline=True)
    embed.add_field(name="💰 Koin", value=f"**{coins}**", inline=True)
    
    embed.add_field(name=f"XP: {xp} / {xp_needed}", value=f"`{bar}`", inline=False)
    embed.add_field(name="🎂 Ulang Tahun", value=display_birthday, inline=False)
    embed.set_footer(text=f"ID Pengguna: {target_user.id}")

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rep", description="Berikan satu poin reputasi kepada pengguna lain (cooldown 24 jam).")
@app_commands.describe(user="Pengguna yang ingin Anda beri reputasi.")
async def rep(interaction: discord.Interaction, user: discord.User):
    giver = interaction.user
    receiver = user

    if giver.id == receiver.id:
        await interaction.response.send_message("❌ Anda tidak bisa memberikan reputasi untuk diri sendiri!", ephemeral=True)
        return
    if receiver.bot:
        await interaction.response.send_message("❌ Anda tidak bisa memberikan reputasi kepada bot!", ephemeral=True)
        return

    giver_data = await bot.db.get_user_data(giver.id)
    now = datetime.now(timezone.utc)
    cooldown = timedelta(hours=24)
    last_rep_time = giver_data.get('last_rep_time')

    if last_rep_time and (now - last_rep_time) < cooldown:
        time_left = cooldown - (now - last_rep_time)
        hours, remainder = divmod(int(time_left.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        await interaction.response.send_message(f"⏳ Anda harus menunggu **{hours} jam {minutes} menit** lagi untuk bisa memberikan reputasi.", ephemeral=True)
        return

    await bot.db.give_reputation(giver_id=giver.id, receiver_id=receiver.id)
    await interaction.response.send_message(f"✅ Anda telah memberikan 1 poin reputasi kepada {receiver.mention}!")

if __name__ ==  "__main__":
    if TOKEN and DATABASE_URL:
        bot.run(TOKEN)
    else:
        print("Error: Pastikan DISCORD_TOKEN dan DATABASE_URL sudah diatur di file .env")
