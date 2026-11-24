import numpy as np
import sounddevice as sd
import librosa
import threading
import time

_running = False
_thread = None
_current_emotion = "neutral"
_speak = None

EMOTIONS = {
    "happy":   {"keywords": ["energia alta", "tono agudo", "ritmo rapido"], "color": "🟡"},
    "sad":     {"keywords": ["energia baja", "tono grave", "ritmo lento"],  "color": "🔵"},
    "angry":   {"keywords": ["energia muy alta", "tono agudo", "brusco"],   "color": "🔴"},
    "calm":    {"keywords": ["energia baja", "tono medio", "ritmo suave"],  "color": "🟢"},
    "neutral": {"keywords": [],                                              "color": "⚪"},
}

def _analyze_audio(audio_data, sample_rate=16000):
    try:
        audio = np.array(audio_data, dtype=np.float32)
        if len(audio) < 1000:
            return "neutral"

        # Energy
        energy = float(np.sqrt(np.mean(audio**2)))

        # Zero crossing rate
        zcr = float(np.mean(librosa.feature.zero_crossing_rate(audio)))

        # Pitch
        pitches, magnitudes = librosa.piptrack(y=audio, sr=sample_rate)
        pitch = float(np.mean(pitches[magnitudes > np.median(magnitudes)]) if magnitudes.max() > 0 else 0)

        # MFCC
        mfcc = librosa.feature.mfcc(y=audio, sr=sample_rate, n_mfcc=13)
        mfcc_mean = float(np.mean(mfcc[0]))

        # Classify emotion
        if energy > 0.08 and zcr > 0.15 and pitch > 200:
            return "angry"
        elif energy > 0.05 and pitch > 150:
            return "happy"
        elif energy < 0.02 and pitch < 100:
            return "sad"
        elif energy < 0.04:
            return "calm"
        else:
            return "neutral"

    except Exception:
        return "neutral"

EMOTION_RESPONSES = {
    "angry":   "Oye genio, noto que estás un poco alterado. ¿Todo bien?",
    "sad":     "Pareces algo bajo de ánimo hoy. ¿Quieres contarme qué pasa?",
    "happy":   "¡Buenas vibras! Se nota que estás de buen humor hoy.",
    "calm":    None,
    "neutral": None,
}

def _detection_loop(speak, player):
    global _running, _current_emotion
    SAMPLE_RATE = 16000
    DURATION = 3
    last_reported = "neutral"
    last_report_time = 0

    while _running:
        try:
            audio = sd.rec(
                int(DURATION * SAMPLE_RATE),
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype='float32'
            )
            sd.wait()
            audio_flat = audio.flatten()
            emotion = _analyze_audio(audio_flat, SAMPLE_RATE)
            _current_emotion = emotion

            now = time.time()
            if (emotion != last_reported and
                emotion in ["angry", "sad", "happy"] and
                now - last_report_time > 30):

                response = EMOTION_RESPONSES.get(emotion)
                if response and speak:
                    speak(response)
                last_reported = emotion
                last_report_time = now

            if player:
                player.write_log(f"SYS: Emoción detectada: {EMOTIONS[emotion]['color']} {emotion.upper()}")

        except Exception as e:
            print(f"[EMOTION] Error: {e}")
            time.sleep(2)

def get_current_emotion() -> str:
    return _current_emotion

def emotion_detector(parameters: dict, player=None, speak=None) -> str:
    global _running, _thread
    action = parameters.get("action", "start")

    if action == "start":
        if _running:
            return "La detección de emociones ya está activa."
        _running = True
        _thread = threading.Thread(
            target=_detection_loop,
            args=(speak, player),
            daemon=True
        )
        _thread.start()
        return "Detección de emociones activada. Analizaré tu estado de ánimo mientras hablamos."

    elif action == "stop":
        _running = False
        return "Detección de emociones desactivada."

    elif action == "status":
        emotion = get_current_emotion()
        return f"Emoción actual: {EMOTIONS[emotion]['color']} {emotion.upper()}"

    return "Acción no reconocida. Usa 'start', 'stop' o 'status'."