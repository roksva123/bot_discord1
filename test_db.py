import asyncio
import asyncpg
import os
import ssl
from dotenv import load_dotenv

# Load .env
load_dotenv(override=True)
url = os.getenv("DATABASE_URL")

async def test_connection():
    print("--- TES KONEKSI DATABASE ---")
    if not url:
        print("❌ Error: DATABASE_URL tidak ditemukan di .env")
        return

    # Cek apakah user tidak sengaja menyalin format JDBC
    if url.startswith("jdbc:"):
        print("❌ Error: URL di .env terdeteksi dalam format JDBC (Java).")
        print("   Python membutuhkan format: postgresql://...")
        print("   Solusi: Hapus 'jdbc:' dari awal string dan pastikan format user:pass@host benar.")
        return

    # Cek apakah ada kurung siku di sekitar password (kesalahan umum copy-paste)
    if ":[" in url and "]@" in url:
        print("❌ Error: Terdeteksi kurung siku '[]' di sekitar password.")
        print("   Jangan gunakan kurung siku di file .env.")
        print("   Salah: ...:[password]@...")
        print("   Benar: ...:password@...")
        return

    # Print the raw URL for debugging, before masking
    print(f"\nDEBUG: Nilai mentah DATABASE_URL dari .env adalah:\n{url}\n")

    # Masking password untuk display
    safe_url = url
    if "@" in url:
        part1, part2 = url.split("@")
        safe_url = f"postgresql://****:****@{part2}"
    
    print(f"Mencoba connect ke: {safe_url}")
    
    try:
        # Coba connect (timeout 10 detik)
        # Buat SSL Context manual untuk mengatasi masalah timeout di Windows
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        
        conn = await asyncio.wait_for(asyncpg.connect(url, ssl=ssl_ctx), timeout=60.0)
        print("✅ BERHASIL! Password benar dan koneksi stabil.")
        
        # Cek versi
        version = await conn.fetchval("SELECT version()")
        print(f"ℹ️  Versi Server: {version}")
        
        await conn.close()
    except asyncpg.InvalidPasswordError:
        print("❌ GAGAL: Password salah! Cek kembali password di .env")
    except asyncpg.exceptions.InternalServerError as e:
        if "Tenant or user not found" in str(e):
            print(f"❌ GAGAL ({type(e).__name__}): {e}")
            print("\n--- ANALISIS ---")
            
            # Deteksi spesifik untuk masalah username pooler
            # Jika menggunakan pooler, username biasanya harus mengandung project ID (misal: postgres.abcdefg)
            if "pooler.supabase.com" in url and "://postgres:" in url:
                print("⚠️  DIAGNOSA: Username tidak lengkap untuk koneksi Pooler.")
                print("    Anda menggunakan host pooler (port 6543) dengan username 'postgres' biasa.")
                print("    Supabase mewajibkan format username: 'postgres.[PROJECT_ID]'")
                print("    -> Silakan salin ulang Connection String dari dashboard Supabase.")

            print("\nCeklis Pemeriksaan:")
            print("1. Hostname: Pastikan bagian `xxx.pooler.supabase.com` sudah benar.")
            print("2. User: Pastikan username sudah benar (seringkali butuh project ID di belakangnya).")
            print("3. Proyek di-pause: Pastikan proyek Supabase Anda dalam keadaan 'Active'.")
        else:
            print(f"❌ ERROR SERVER ({type(e).__name__}): {e}")
    except Exception as e:
        print(f"❌ ERROR LAIN ({type(e).__name__}): {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())
