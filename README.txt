=== TUTORIAL DEPLOY KE LEAPCELL.IO ===

1. Persiapan GitHub:
   - Upload semua file di folder ini ke repository GitHub kamu (bisa Public/Private).
   - Pastikan file "requirements.txt" ada di sana.

2. Setup Leapcell:
   - Buka https://leapcell.io/ dan Login (pakai GitHub).
   - Klik "Create Service".
   - Pilih repository GitHub bot kamu.
   - Beri nama service (bebas).

3. Konfigurasi (Penting!):
   - Runtime: Python 3
   - Build Command: pip install -r requirements.txt
   - Start Command: python main.py
   - Port: 8080

4. Environment Variables (Wajib diisi di Dashboard Leapcell):
   - Masuk ke tab "Environment" atau "Settings".
   - Tambahkan Variable:
     > DISCORD_TOKEN  = (Isi dengan token bot kamu)
     > DATABASE_URL   = (Isi dengan link database Supabase)
   - Klik Deploy / Save.