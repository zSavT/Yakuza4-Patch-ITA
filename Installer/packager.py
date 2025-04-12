import os
import pyzipper
import argparse
import shutil

AES_KEY = b"^#Nxu9cNV2722HA&jw4H3j7sXnt&#X"  # Chiave AES integrata (32 byte per AES-256)

def get_input_path(prompt_msg):
    while True:
        path = input(prompt_msg).strip()
        if os.path.isdir(path):
            return path
        print("❌ Percorso non valido. Riprova.")

def get_output_filename():
    name = input("📦 Nome del file di output (senza estensione): ").strip()
    return f"{name}.pkg"

def confirm(prompt_msg):
    return input(prompt_msg + " [s/N]: ").lower() == 's'

def create_encrypted_package(source_folder, output_file):
    with pyzipper.AESZipFile(output_file, 'w', compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
        zf.setencryption(pyzipper.WZ_AES, nbits=256)
        zf.setpassword(AES_KEY)

        for foldername, subfolders, filenames in os.walk(source_folder):
            for filename in filenames:
                filepath = os.path.join(foldername, filename)
                arcname = os.path.relpath(filepath, source_folder)
                zf.write(filepath, arcname)

    print(f"\n✅ Pacchetto criptato creato: {output_file}")

if __name__ == "__main__":
    print("\n🔐 Builder CLI per creare un pacchetto criptato")
    source = get_input_path("📁 Inserisci il percorso della cartella da includere: ")
    output = get_output_filename()

    print(f"\n📋 Riepilogo:")
    print(f"  - Cartella da includere: {source}")
    print(f"  - File in uscita:        {output}")

    if confirm("Procedere con la creazione del pacchetto?"):
        create_encrypted_package(source, output)
    else:
        print("⏹️  Operazione annullata.")