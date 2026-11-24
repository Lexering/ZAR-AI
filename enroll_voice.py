import sounddevice as sd
import numpy as np
import pickle
from resemblyzer import VoiceEncoder, preprocess_wav

encoder = VoiceEncoder()

print("Vamos a grabar tu voz como referencia.")
print("Habla durante 5 segundos cuando veas 'Grabando...'")
input("Pulsa ENTER para empezar...")

print("Grabando...")
audio = sd.rec(int(5 * 16000), samplerate=16000, channels=1, dtype='float32')
sd.wait()
print("Grabación terminada.")

wav = preprocess_wav(audio.flatten(), source_sr=16000)
embed = encoder.embed_utterance(wav)

with open("my_voice.pkl", "wb") as f:
    pickle.dump(embed, f)

print("✅ Tu voz ha sido guardada en my_voice.pkl")