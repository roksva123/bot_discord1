import asyncpg
import datetime
import ssl

class DatabaseManager:
    def __init__(self, dsn: str):
        """
        Manajer Database untuk koneksi PostgreSQL.
        :param dsn: Data Source Name (Connection URL) untuk database.
        """
        self.dsn = dsn
        self._pool = None

    async def connect(self):
        """Membuat connection pool."""
        if not self._pool:
            try:
                # Buat SSL Context manual untuk mengatasi masalah timeout di Windows
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

                self._pool = await asyncpg.create_pool(
                    dsn=self.dsn,
                    command_timeout=60,
                    statement_cache_size=0,
                    ssl=ssl_ctx
                )
                print("✅ Berhasil terhubung ke database PostgreSQL.")
            except asyncpg.exceptions.InternalServerError as e:
                print("❌ FATAL: Gagal membuat koneksi pool ke database.")
                if "Tenant or user not found" in str(e):
                    print("\n--- ANALISIS KESALAHAN KONEKSI SUPABASE ---")
                    print("Error 'Tenant or user not found' mengindikasikan ada kesalahan pada variabel 'DATABASE_URL' di file .env Anda.")
                    print("Pastikan detail berikut sudah benar:")
                    print(" - Hostname (bagian ...pooler.supabase.com), Username, Password, dan Port.")
                    print(" - Pastikan juga proyek Supabase Anda tidak sedang di-pause.")
                    print("\nBot tidak dapat dimulai tanpa koneksi database yang valid. Harap perbaiki .env dan restart bot.")
                raise  # Hentikan eksekusi bot
            except Exception as e:
                print(f"❌ FATAL: Terjadi error tak terduga saat menghubungkan ke database: {e}")
                raise

    async def close(self):
        """Menutup connection pool."""
        if self._pool:
            await self._pool.close()
            print("🔌 Koneksi ke database PostgreSQL ditutup.")

    async def init_db(self):
        """Membuat dan memodifikasi tabel jika diperlukan."""
        async with self._pool.acquire() as connection:
            await connection.execute("""
                CREATE TABLE IF NOT EXISTS economy (
                    user_id BIGINT PRIMARY KEY,
                    coins BIGINT DEFAULT 100,
                    last_daily TIMESTAMPTZ
                );
            """)
            # Tambahkan kolom birthday jika belum ada
            await connection.execute("""
                ALTER TABLE economy ADD COLUMN IF NOT EXISTS birthday TEXT;
            """)
            # PART 5: Tambahkan kolom untuk level, xp, dan reputasi
            await connection.execute("ALTER TABLE economy ADD COLUMN IF NOT EXISTS level INT DEFAULT 1;")
            await connection.execute("ALTER TABLE economy ADD COLUMN IF NOT EXISTS xp INT DEFAULT 0;")
            await connection.execute("ALTER TABLE economy ADD COLUMN IF NOT EXISTS reputation INT DEFAULT 0;")
            await connection.execute("ALTER TABLE economy ADD COLUMN IF NOT EXISTS last_rep_time TIMESTAMPTZ;")
            await connection.execute("ALTER TABLE economy ADD COLUMN IF NOT EXISTS last_xp_time TIMESTAMPTZ;")
            print("🛠️  Tabel 'economy' siap digunakan.")

    async def get_user_data(self, user_id: int):
        """
        Mengambil data user. Jika user belum ada, buat entri baru.
        Ini adalah pola 'upsert' yang efisien.
        """
        async with self._pool.acquire() as connection:
            # Coba ambil data user
            user_data = await connection.fetchrow("SELECT * FROM economy WHERE user_id = $1", user_id)
            
            # Jika tidak ada, buat entri baru dan ambil lagi
            if not user_data:
                await connection.execute(
                    "INSERT INTO economy (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
                    user_id
                )
                user_data = await connection.fetchrow("SELECT * FROM economy WHERE user_id = $1", user_id)
            
            return user_data

    async def update_user_balance(self, user_id: int, coins: int, last_daily: datetime.datetime = None):
        """Memperbarui saldo koin dan/atau waktu daily claim."""
        async with self._pool.acquire() as connection:
            if last_daily:
                await connection.execute("UPDATE economy SET coins = $1, last_daily = $2 WHERE user_id = $3", coins, last_daily, user_id)
            else:
                await connection.execute("UPDATE economy SET coins = $1 WHERE user_id = $2", coins, user_id)

    async def set_birthday(self, user_id: int, birthday_str: str):
        """Menyimpan tanggal ulang tahun user (format MM-DD)."""
        # Pastikan user ada di DB dulu
        await self.get_user_data(user_id)
        async with self._pool.acquire() as connection:
            await connection.execute(
                "UPDATE economy SET birthday = $1 WHERE user_id = $2",
                birthday_str, user_id
            )

    async def get_birthdays_today(self, today_str: str):
        """Mengambil semua user yang ulang tahun hari ini (format MM-DD)."""
        async with self._pool.acquire() as connection:
            users = await connection.fetch("SELECT user_id FROM economy WHERE birthday = $1", today_str)
            return users

    async def grant_xp(self, user_id: int, xp_to_add: int):
        """Memberikan XP kepada user dan mencatat waktu."""
        async with self._pool.acquire() as connection:
            await connection.execute(
                "UPDATE economy SET xp = xp + $1, last_xp_time = $2 WHERE user_id = $3",
                xp_to_add, datetime.datetime.now(datetime.timezone.utc), user_id
            )

    async def update_level(self, user_id: int, new_level: int, new_xp: int):
        """Mengupdate level dan xp user setelah naik level."""
        async with self._pool.acquire() as connection:
            await connection.execute(
                "UPDATE economy SET level = $1, xp = $2 WHERE user_id = $3",
                new_level, new_xp, user_id
            )

    async def give_reputation(self, giver_id: int, receiver_id: int):
        """Memberikan reputasi dari satu user ke user lain dan mencatat waktunya."""
        async with self._pool.acquire() as connection:
            # Tambah reputasi ke penerima
            await connection.execute("UPDATE economy SET reputation = reputation + 1 WHERE user_id = $1", receiver_id)
            # Catat waktu cooldown untuk pemberi
            await connection.execute("UPDATE economy SET last_rep_time = $1 WHERE user_id = $2", datetime.datetime.now(datetime.timezone.utc), giver_id)