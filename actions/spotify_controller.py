import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"

def _get_spotify():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f).get("spotify", {})
    
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        redirect_uri=cfg["redirect_uri"],
        scope="user-modify-playback-state user-read-playback-state user-read-currently-playing playlist-read-private"
    ))
    return sp

def spotify_controller(parameters: dict, player=None) -> str:
    action = parameters.get("action", "play")
    query  = parameters.get("query", "")

    try:
        sp = _get_spotify()

        if action == "play":
            if query:
                results = sp.search(q=query, type="track", limit=1)
                tracks  = results["tracks"]["items"]
                if not tracks:
                    return f"No encontré '{query}' en Spotify."
                uri  = tracks[0]["uri"]
                name = tracks[0]["name"]
                artist = tracks[0]["artists"][0]["name"]
                sp.start_playback(uris=[uri])
                return f"Reproduciendo '{name}' de {artist}."
            else:
                sp.start_playback()
                return "Reproducción iniciada."

        elif action == "pause":
            sp.pause_playback()
            return "Música pausada."

        elif action == "resume":
            sp.start_playback()
            return "Música reanudada."

        elif action == "next":
            sp.next_track()
            return "Siguiente canción."

        elif action == "previous":
            sp.previous_track()
            return "Canción anterior."

        elif action == "volume":
            vol = int(parameters.get("volume", 50))
            sp.volume(vol)
            return f"Volumen ajustado a {vol}%."

        elif action == "current":
            track = sp.current_playback()
            if not track or not track.get("item"):
                return "No hay nada reproduciéndose ahora mismo."
            name   = track["item"]["name"]
            artist = track["item"]["artists"][0]["name"]
            return f"Ahora suena '{name}' de {artist}."

        elif action == "playlist":
            playlists = sp.current_user_playlists(limit=5)
            names = [p["name"] for p in playlists["items"]]
            return "Tus playlists: " + ", ".join(names)

        elif action == "shuffle":
            state = parameters.get("state", True)
            sp.shuffle(state)
            return f"Shuffle {'activado' if state else 'desactivado'}."

        elif action == "repeat":
            mode = parameters.get("mode", "track")
            sp.repeat(mode)
            return f"Repeat ajustado a {mode}."

        else:
            return f"Acción '{action}' no reconocida."

    except Exception as e:
        return f"Error con Spotify: {e}"