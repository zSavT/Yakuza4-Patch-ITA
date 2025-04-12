import os
import pyzipper
import sys 

KEY_FILENAME = "chiave.txt" 

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

def create_encrypted_package(source_folder, output_file, encryption_key):
    """Crea un archivio ZIP criptato con AES-256."""
    print(f"\n⚙️  Creazione pacchetto criptato: {output_file}...")
    try:
        with pyzipper.AESZipFile(output_file, 'w',
                                 compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as zf:
            zf.setencryption(pyzipper.WZ_AES, nbits=256)
            zf.setpassword(encryption_key)

            for foldername, subfolders, filenames in os.walk(source_folder):
                for filename in filenames:
                    filepath = os.path.join(foldername, filename)
                    arcname = os.path.relpath(filepath, source_folder)
                    print(f"   -> Aggiungendo: {arcname}")
                    zf.write(filepath, arcname)

        print(f"\n✅ Pacchetto criptato creato con successo: {output_file}")

    except Exception as e:
        print(f"\n❌ Errore durante la creazione del pacchetto: {e}")
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
                print(f"   🗑️  File parziale '{output_file}' rimosso.")
            except OSError as remove_error:
                print(f"   ⚠️  Impossibile rimuovere il file parziale '{output_file}': {remove_error}")
        sys.exit(1) 


if __name__ == "__main__":
    print("\n🔐 Builder CLI per creare un pacchetto criptato")
    aes_key_from_file = None
    try:
        with open(KEY_FILENAME, 'r', encoding='utf-8') as f_key:
            key_str = f_key.readline().strip()
        if not key_str:
            print(f"❌ Errore: Il file della chiave '{KEY_FILENAME}' è vuoto.")
            sys.exit(1) 
        aes_key_from_file = key_str.encode('utf-8')
        if len(aes_key_from_file) != 32:
            print(f"⚠️ Attenzione: La chiave nel file '{KEY_FILENAME}' è lunga {len(aes_key_from_file)} byte.")
            print("     AES-256 richiede una chiave di 32 byte per la massima sicurezza.")


    except FileNotFoundError:
        print(f"❌ Errore: File della chiave '{KEY_FILENAME}' non trovato.")
        print(f"     Assicurati che il file esista nella stessa directory dello script e contenga la chiave sulla prima riga.")
        sys.exit(1) 
    except Exception as e:
        print(f"❌ Errore durante la lettura del file della chiave '{KEY_FILENAME}': {e}")
        sys.exit(1) 

    source = get_input_path("📁 Inserisci il percorso della cartella da includere: ")
    output = get_output_filename()

    print(f"\n📋 Riepilogo:")
    print(f"   - Cartella da includere: {source}")
    print(f"   - File in uscita:       {output}")
    print(f"   - File chiave:          {KEY_FILENAME}") 


    if confirm("\nProcedere con la creazione del pacchetto?"):
        create_encrypted_package(source, output, aes_key_from_file)
    else:
        print("\n⏹️  Operazione annullata.")