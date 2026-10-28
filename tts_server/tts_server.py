from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from pathlib import Path
import hashlib
import io

import edge_tts

try:
    from gtts import gTTS
except ImportError:
    gTTS = None

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Spoken sentences are saved here, so they still play without internet
CACHE = Path("tts_cache")
CACHE.mkdir(exist_ok=True)


class SpeechRequest(BaseModel):
    text: str
    language: str


EDGE_VOICES = {
    "bn": "bn-IN-TanishaaNeural",
    "mr": "mr-IN-AarohiNeural",
}
GTTS_LANGS = {"bn": "bn", "mr": "mr"}


async def edge_audio(text: str, lang: str) -> bytes:
    communicate = edge_tts.Communicate(text, EDGE_VOICES[lang])
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    return buf.getvalue()


def gtts_audio(text: str, lang: str) -> bytes:
    if gTTS is None:
        raise RuntimeError("gTTS is not installed")
    buf = io.BytesIO()
    gTTS(text=text, lang=GTTS_LANGS[lang]).write_to_fp(buf)
    return buf.getvalue()


@app.get("/")
def home():
    return {"status": "ok", "message": "Regional TTS server is running"}


@app.post("/speak")
async def speak(request: SpeechRequest):
    lang = request.language
    if lang not in EDGE_VOICES:
        raise HTTPException(status_code=400, detail="Unsupported language")

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")

    # 1) Already spoken before? Play it from disk (works offline)
    key = hashlib.sha1((lang + "|" + text).encode("utf-8")).hexdigest()
    cached = CACHE / (key + ".mp3")
    if cached.exists():
        return Response(content=cached.read_bytes(), media_type="audio/mpeg")

    audio = b""

    # 2) Try Microsoft Edge voice
    try:
        audio = await edge_audio(text, lang)
    except Exception as e:
        print("edge-tts failed:", type(e).__name__)

    # 3) If that failed, try Google voice
    if not audio:
        try:
            audio = await run_in_threadpool(gtts_audio, text, lang)
            print("Used gTTS fallback")
        except Exception as e:
            print("gTTS failed:", type(e).__name__)

    if not audio:
        raise HTTPException(status_code=502, detail="No voice service available")

    cached.write_bytes(audio)
    return Response(content=audio, media_type="audio/mpeg")
