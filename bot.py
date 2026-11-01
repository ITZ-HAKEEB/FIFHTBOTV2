"""
🎵 Audio Controller v7 - Ultra Premium
Extra Extreme Power + Unlimited Echo
Advanced Powerful Audio Processing with ZERO-LATENCY
"""
import numpy as np
import os
import re
import json
import logging
import asyncio
import warnings
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import threading

from config import API_ID, API_HASH, BOT_TOKEN, OWNER_ID, STRING_SESSION, RECORD_GROUP

warnings.filterwarnings('ignore')

# Try to import scipy, fallback to basic processing if not available
try:
    from scipy import signal
    from scipy.signal import butter, lfilter, sosfilt, sosfreqz
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("⚠️ Scipy not available - basic audio processing only")

from pyrogram import Client, filters as pyro_filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode, ChatType
from pyrogram.errors import PeerIdInvalid

from pytgcalls import PyTgCalls, idle
from pytgcalls import filters as pytg_filters
from pytgcalls.types import (
    Device,
    Direction,
    ExternalMedia,
    MediaStream,
    RecordStream,
    StreamFrames
)
from pytgcalls.types.raw import AudioParameters
from pytgcalls.exceptions import NoActiveGroupCall

# ==================== LOGGING CONFIG ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Disable noisy logs
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("pytgcalls").setLevel(logging.WARNING)

# MAX BITRATE - 512kbps for CD quality
AUDIO_PARAMETERS = AudioParameters(bitrate=512000, channels=2)

# ==================== CLIENTS ====================
bot_app = Client("botn_v7", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user_app = Client("useron_v7", api_id=API_ID, api_hash=API_HASH, session_string=STRING_SESSION)
call_py = PyTgCalls(user_app)

# STATE
forward_chats = set()
is_muted = False
is_recording = False
RECORD_SOURCE = RECORD_GROUP
processing_lock = asyncio.Lock()
bot_start_time = time.time()

# USER MANAGEMENT
approved_users = set()

# OPTIMIZED: Pre-allocated audio processing buffer pool
BUFFER_POOL_SIZE = 8
AUDIO_BUFFER_POOL = [np.zeros(960, dtype=np.float32) for _ in range(BUFFER_POOL_SIZE)]
BUFFER_POOL_LOCK = threading.Lock()
BUFFER_POOL_INDEX = 0

# THREAD POOL for parallel processing
EXECUTOR = ThreadPoolExecutor(max_workers=4)

# ==================== AUDIO CONFIG ====================
audio_config = {
    'volume': 100,
    'bass': 0,
    'treble': 0,
    'gain': 0,
    'compressor': True,
    'limiter': True,
    'highpass': False,
    'lowpass': False,
    # UNLIMITED ECHO
    'echo_delay': 70,          # ms (0-5000+)
    'echo_decay': 0.32,        # attenuation (0-5.0)
    'freq_shift': 2.0,         # Hz (0-100+)
    'pre_gain': 1.0,           # input multiplier (0.1-10.0)
    # Overdrive / Distortion
    'overdrive': 0,            # 0-100
    'overdrive_gain': 1.0,     # 1.0-10.0
    'overdrive_tone': 0.5,     # 0.0-1.0
    # Reverb
    'reverb_room': 0,          # 0-100
    'reverb_damp': 0.5,        # 0.0-1.0
    # Stereo Width
    'stereo_width': 0,         # -100 to 100
}

# ==================== DYNAMIC CLIENTS ====================
dynamic_clients = {}
dynamic_clients_lock = asyncio.Lock()

# ==================== PERSISTENCE ====================
STATE_FILE = "bot_state.json"

def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({
                "approved_users": sorted(approved_users),
                "audio_config": audio_config,
                "record_source": RECORD_SOURCE,
            }, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving state: {e}")

def load_state():
    global RECORD_SOURCE
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        approved_users.update(int(uid) for uid in data.get("approved_users", []))
        saved_config = data.get("audio_config")
        if isinstance(saved_config, dict):
            for k, v in saved_config.items():
                if k in audio_config:
                    audio_config[k] = v
        saved_source = data.get("record_source")
        if saved_source is not None:
            RECORD_SOURCE = saved_source
        logger.info(f"Loaded state: {len(approved_users)} approved user(s), source: {RECORD_SOURCE}")
    except FileNotFoundError:
        logger.info("No saved state found - starting fresh")
    except Exception as e:
        logger.error(f"Error loading state: {e}")

# ==================== AUTHORIZATION ====================
async def _authorized_filter(_, __, message):
    user_id = message.from_user.id if message.from_user else None
    if user_id == OWNER_ID or user_id in approved_users:
        return True
    logger.info(f"Unauthorized access from {user_id} - ignoring")
    return False

def authorized_only():
    return pyro_filters.create(_authorized_filter)

# ==================== UTILITY FUNCTIONS ====================
def escape_markdown(text):
    if not text:
        return ""
    specials = "`*_["
    return "".join("\\" + c if c in specials else c for c in text)

def get_uptime():
    uptime_seconds = int(time.time() - bot_start_time)
    days = uptime_seconds // 86400
    hours = (uptime_seconds % 86400) // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60
    if days > 0:
        return f"{days}d {hours}h {minutes}m {seconds}s"
    elif hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"

def create_progress_bar(value, max_val, length=12):
    filled = int((value / max_val) * length) if max_val > 0 else 0
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)

def get_effect_status(value, max_val):
    if max_val <= 0:
        return "⚫ OFF"
    percentage = (value / max_val) * 100
    if percentage == 0:
        return "⚫ OFF"
    elif percentage <= 30:
        return "🟢 LIGHT"
    elif percentage <= 60:
        return "🟡 MEDIUM"
    elif percentage <= 80:
        return "🟠 STRONG"
    else:
        return "🔴 EXTREME"

# ==================== AUDIO PROCESSING PIPELINE ====================
_filter_cache = {}

def get_biquad_coeffs(filter_type, freq, Q=0.707, gain_db=0, sample_rate=48000):
    cache_key = f"{filter_type}_{freq}_{Q}_{gain_db}_{sample_rate}"
    if cache_key in _filter_cache:
        return _filter_cache[cache_key]
    
    try:
        if filter_type == 'lowshelf':
            b, a = signal.biquad(freq, Q, gain_db, 'lowshelf', sample_rate)
        elif filter_type == 'highshelf':
            b, a = signal.biquad(freq, Q, gain_db, 'highshelf', sample_rate)
        elif filter_type == 'lowpass':
            b, a = signal.biquad(freq, Q, gain_db, 'lowpass', sample_rate)
        elif filter_type == 'highpass':
            b, a = signal.biquad(freq, Q, gain_db, 'highpass', sample_rate)
        else:
            b, a = signal.biquad(freq, Q, gain_db, 'peaking', sample_rate)
        _filter_cache[cache_key] = (b, a)
        return b, a
    except Exception:
        return np.array([1.0]), np.array([1.0])

def apply_volume_boost_vectorized(audio_data, level):
    if level == 100:
        return audio_data
    gain_factor = level / 50.0
    return np.clip(audio_data.astype(np.float32) * gain_factor, -32768, 32767).astype(np.int16)

def apply_bass_boost_optimized(audio_data, bass_level, sample_rate=48000):
    if bass_level == 0:
        return audio_data
    try:
        gain_db = bass_level / 2.5
        b, a = get_biquad_coeffs('lowshelf', 80, 0.8, gain_db, sample_rate)
        audio_float = audio_data.astype(np.float32)
        filtered = signal.lfilter(b, a, audio_float)
        return np.clip(filtered, -32768, 32767).astype(np.int16)
    except Exception as e:
        logger.error(f"Advanced bass error: {e}")
        audio = audio_data.astype(np.float32)
        window_size = max(12, min(48 - int(bass_level * 0.4), 48))
        kernel = np.ones(window_size, dtype=np.float32) / window_size
        low = np.convolve(audio, kernel, mode="same")
        mix = min(0.25, bass_level / 300.0)
        processed = audio + (low * mix)
        processed -= np.mean(processed)
        processed = 30000.0 * np.tanh(processed / 30000.0)
        return np.clip(processed, -32768, 32767).astype(np.int16)

def apply_treble_boost_optimized(audio_data, treble_level, sample_rate=48000):
    if treble_level == 0:
        return audio_data
    try:
        gain_db = treble_level / 4.0
        b, a = get_biquad_coeffs('highshelf', 4200, 0.8, gain_db, sample_rate)
        audio_float = audio_data.astype(np.float32)
        filtered = signal.lfilter(b, a, audio_float)
        return np.clip(filtered, -32768, 32767).astype(np.int16)
    except Exception as e:
        logger.error(f"Advanced treble error: {e}")
        audio = audio_data.astype(np.float32)
        alpha = 0.92
        high = np.empty_like(audio)
        high[0] = audio[0]
        for i in range(1, len(audio)):
            high[i] = alpha * (high[i - 1] + audio[i] - audio[i - 1])
        mix = min(0.35, treble_level / 180.0)
        processed = audio + (high * mix)
        processed = 30000.0 * np.tanh(processed / 30000.0)
        return np.clip(processed, -32768, 32767).astype(np.int16)

def apply_overdrive(audio_data, overdrive_level, gain=1.0, tone=0.5):
    if overdrive_level == 0:
        return audio_data
    audio = audio_data.astype(np.float32)
    norm_factor = 32767.0
    audio_norm = audio / norm_factor
    
    drive = 0.5 + (overdrive_level / 200.0)
    gain_scaled = gain * (1.0 + overdrive_level / 50.0)
    
    pos_threshold = 0.5 / drive
    neg_threshold = 0.7 / drive
    
    pos_mask = audio_norm > pos_threshold
    audio_norm[pos_mask] = 1.0 - np.exp(-(audio_norm[pos_mask] - pos_threshold) * drive * 2)
    
    neg_mask = audio_norm < -neg_threshold
    audio_norm[neg_mask] = -1.0 + np.exp((audio_norm[neg_mask] + neg_threshold) * drive * 1.5)
    
    if tone != 0.5:
        tone_factor = 1.0 + (tone - 0.5) * 0.8
        alpha = 0.85 + (tone * 0.14)
        high = np.empty_like(audio_norm)
        high[0] = audio_norm[0]
        for i in range(1, len(audio_norm)):
            high[i] = alpha * (high[i-1] + audio_norm[i] - audio_norm[i-1])
        audio_norm = audio_norm + (high * (tone_factor - 1.0) * 0.3)
    
    audio_norm *= gain_scaled
    audio_norm = np.clip(audio_norm, -1.0, 1.0)
    return (audio_norm * norm_factor).astype(np.int16)

def apply_reverb(audio_data, room_size, damp=0.5):
    if room_size == 0:
        return audio_data
    audio = audio_data.astype(np.float32)
    sample_rate = 48000
    
    delay_ms = 30 + (room_size * 2)
    decay = 0.3 + (room_size / 250.0)
    
    delay_samples = int(sample_rate * delay_ms / 1000)
    delay_samples = min(max(delay_samples, 1), sample_rate)
    
    buffer = np.zeros(delay_samples, dtype=np.float32)
    output = np.zeros_like(audio)
    idx = 0
    
    for i in range(len(audio)):
        delayed = buffer[idx]
        output[i] = audio[i] + delayed * decay
        buffer[idx] = audio[i] + delayed * decay * 0.5
        idx = (idx + 1) % delay_samples
    
    wet = min(room_size / 150.0, 0.8)
    output = audio * (1 - wet) + output * wet
    return np.clip(output, -32768, 32767).astype(np.int16)

def apply_stereo_width(audio_data, width):
    if width == 0 or len(audio_data.shape) < 2:
        return audio_data
    if audio_data.shape[1] == 2:
        left = audio_data[:, 0].astype(np.float32)
        right = audio_data[:, 1].astype(np.float32)
        
        mid = (left + right) / 2.0
        side = (left - right) / 2.0
        
        side_gain = 1.0 + (width / 100.0)
        side = np.clip(side * side_gain, -32768, 32767)
        
        new_left = np.clip(mid + side, -32768, 32767)
        new_right = np.clip(mid - side, -32768, 32767)
        return np.column_stack([new_left.astype(np.int16), new_right.astype(np.int16)])
    return audio_data

def apply_soft_gain_optimized(audio_data, gain_level):
    if gain_level <= 0:
        return audio_data
    audio = audio_data.astype(np.float32)
    gain_factor = 2.0 + (gain_level / 40.0)
    processed = audio * gain_factor
    
    if audio_config.get('compressor', True):
        threshold = 18000.0
        ratio = 3.5
        abs_processed = np.abs(processed)
        above = abs_processed > threshold
        if np.any(above):
            excess = abs_processed[above] - threshold
            processed[above] = np.sign(processed[above]) * (threshold + excess / ratio)
        processed *= 1.55
    
    if audio_config.get('limiter', True):
        limit = 32700.0
        processed = limit * np.tanh(processed / limit)
    
    return np.clip(processed, -32768, 32767).astype(np.int16)

def apply_echo_optimized(audio_data, echo_state, config):
    delay_ms = max(1, config.get('echo_delay', 70))
    decay = max(0.01, config.get('echo_decay', 0.32))
    
    sample_rate = 48000
    delay_samples = int(sample_rate * delay_ms / 1000)
    max_buffer_size = int(sample_rate * 5)
    delay_samples = max(1, min(delay_samples, max_buffer_size))
    
    buffer = echo_state.get('buffer')
    if buffer is None or len(buffer) != delay_samples:
        buffer = np.zeros(delay_samples, dtype=np.float32)
        echo_state['buffer'] = buffer
        echo_state['index'] = 0
    
    idx = echo_state['index']
    out = audio_data.astype(np.float32)
    
    if len(out) <= delay_samples:
        chunk_size = min(len(out), 512)
        for start in range(0, len(out), chunk_size):
            end = min(start + chunk_size, len(out))
            chunk_out = out[start:end]
            for i in range(len(chunk_out)):
                delayed = buffer[idx]
                chunk_out[i] = chunk_out[i] + delayed * decay
                buffer[idx] = np.clip(chunk_out[i], -32768, 32767)
                idx = (idx + 1) % delay_samples
            out[start:end] = chunk_out
    else:
        for i in range(len(out)):
            delayed = buffer[idx]
            out[i] = out[i] + delayed * decay
            buffer[idx] = np.clip(out[i], -32768, 32767)
            idx = (idx + 1) % delay_samples
            
    echo_state['index'] = idx
    return np.clip(out, -32768, 32767).astype(np.int16)

def apply_frequency_shifter_optimized(audio_data, config):
    shift_hz = config.get('freq_shift', 2.0)
    if shift_hz <= 0:
        return audio_data
    audio = audio_data.astype(np.float32)
    n = np.arange(len(audio))
    mod = 1.0 + 0.001 * np.sin(2 * np.pi * shift_hz * n / 48000)
    out = np.clip(audio * mod, -32768, 32767)
    return out.astype(np.int16)

def process_audio_pipeline(audio_data, config=None):
    if config is None:
        config = audio_config
    if audio_data is None or len(audio_data) == 0:
        return audio_data
    
    try:
        processed = audio_data.copy()
        pre_gain = config.get('pre_gain', 1.0)
        if pre_gain != 1.0:
            processed = np.clip(processed.astype(np.float32) * pre_gain, -32768, 32767).astype(np.int16)

        if config['volume'] != 100:
            processed = apply_volume_boost_vectorized(processed, config['volume'])

        if config['bass'] > 0:
            processed = apply_bass_boost_optimized(processed, config['bass'])

        if config['treble'] > 0:
            processed = apply_treble_boost_optimized(processed, config['treble'])

        if config['gain'] > 0:
            processed = apply_soft_gain_optimized(processed, config['gain'])

        if config.get('overdrive', 0) > 0:
            processed = apply_overdrive(
                processed, 
                config['overdrive'],
                config.get('overdrive_gain', 1.0),
                config.get('overdrive_tone', 0.5)
            )

        if config.get('reverb_room', 0) > 0:
            processed = apply_reverb(processed, config['reverb_room'], config.get('reverb_damp', 0.5))

        if config.get('lowpass', False) and SCIPY_AVAILABLE:
            try:
                b, a = get_biquad_coeffs('lowpass', 12000, 0.707, 0, 48000)
                processed = signal.lfilter(b, a, processed.astype(np.float32))
                processed = np.clip(processed, -32768, 32767).astype(np.int16)
            except Exception:
                pass

        if config.get('highpass', False) and SCIPY_AVAILABLE:
            try:
                b, a = get_biquad_coeffs('highpass', 40, 0.707, 0, 48000)
                processed = signal.lfilter(b, a, processed.astype(np.float32))
                processed = np.clip(processed, -32768, 32767).astype(np.int16)
            except Exception:
                pass

        if config.get('freq_shift', 0) > 0:
            processed = apply_frequency_shifter_optimized(processed, config)

        if config.get('stereo_width', 0) != 0:
            processed = apply_stereo_width(processed, config['stereo_width'])

        limit = 32700.0
        processed = limit * np.tanh(processed.astype(np.float32) / limit)
        return np.clip(processed, -32768, 32767).astype(np.int16)

    except Exception as e:
        logger.error(f"Audio processing error: {e}")
        return audio_data

# ==================== MAIN AUDIO FORWARDER ====================
@call_py.on_update(pytg_filters.stream_frame(Direction.INCOMING, Device.MICROPHONE))
async def audio_forwarder(_, update: StreamFrames):
    if is_muted or update.chat_id != RECORD_SOURCE or not forward_chats:
        return
    
    async with processing_lock:
        try:
            if not update.frames:
                return
            
            frame_length = len(update.frames[0].frame) // 2
            mixed_acc = np.zeros(frame_length, dtype=np.int32)
            valid_frames = 0
            
            for frame_data in update.frames:
                try:
                    source_samples = np.frombuffer(frame_data.frame, dtype=np.int16)
                    if len(source_samples) == frame_length:
                        mixed_acc += source_samples.astype(np.int32)
                        valid_frames += 1
                except Exception:
                    continue
            
            if valid_frames == 0:
                return
            
            mixed_acc //= valid_frames
            mixed_output = np.clip(mixed_acc, -32768, 32767).astype(np.int16)
            
            processed_output = process_audio_pipeline(mixed_output, audio_config)
            
            global_echo_state = getattr(audio_forwarder, 'echo_state', None)
            if global_echo_state is None:
                global_echo_state = {}
                audio_forwarder.echo_state = global_echo_state
            processed_output = apply_echo_optimized(processed_output, global_echo_state, audio_config)
            
            mixed_bytes = processed_output.tobytes()
            
            for chat_id in list(forward_chats):
                try:
                    await call_py.send_frame(chat_id, Device.MICROPHONE, mixed_bytes)
                except Exception as e:
                    logger.debug(f"Send error to {chat_id}: {e}")
                    if "not found" in str(e).lower() or "invalid" in str(e).lower():
                        forward_chats.discard(chat_id)
                        logger.warning(f"Removed {chat_id} from forwarding list")
                        
        except Exception as e:
            logger.error(f"Audio handler error: {e}")

# ==================== CACHE CHAT ====================
async def cache_chat_info(chat_id):
    try:
        logger.info(f"Caching chat info for {chat_id}...")
        try:
            chat = await user_app.get_chat(chat_id)
            chat_title = chat.title if hasattr(chat, 'title') else str(chat_id)
            logger.info(f"✅ Cached: {chat_id} - {chat_title}")
            return True
        except (PeerIdInvalid, ValueError) as e:
            logger.debug(f"Direct get_chat failed: {e}")
            try:
                async for dialog in user_app.get_dialogs(limit=50):
                    if dialog.chat.id == chat_id:
                        logger.info(f"✅ Found in dialogs: {chat_id}")
                        return True
            except Exception:
                pass
            logger.error(f"❌ Cannot find chat: {chat_id}")
            return False
    except Exception as e:
        logger.error(f"Cache error for {chat_id}: {e}")
        return False

async def join_call_safe(chat_id, call_py_obj=None, user_app_obj=None):
    if call_py_obj is None:
        call_py_obj = call_py
    if user_app_obj is None:
        user_app_obj = user_app
    try:
        if not await cache_chat_info(chat_id):
            return False, "Chat not found or inaccessible"
        try:
            await call_py_obj.play(
                chat_id,
                MediaStream(ExternalMedia.AUDIO, AUDIO_PARAMETERS),
            )
            return True, None
        except NoActiveGroupCall:
            return False, "No active voice chat"
        except Exception as e:
            error_msg = str(e)
            if "already participating" in error_msg.lower():
                return True, None
            return False, error_msg
    except Exception as e:
        return False, str(e)

# ==================== DYNAMIC AUDIO FORWARDER ====================
async def dynamic_audio_forwarder(client_data):
    call_py_obj = client_data['call_py']
    forward_chats_set = client_data['forward_chats']
    record_source = client_data['record_source']
    is_muted_local = client_data['is_muted']
    config = client_data['audio_config']
    lock = client_data['lock']
    echo_state = {}

    @call_py_obj.on_update(pytg_filters.stream_frame(Direction.INCOMING, Device.MICROPHONE))
    async def handler(_, update: StreamFrames):
        if is_muted_local or update.chat_id != record_source or not forward_chats_set:
            return
        async with lock:
            try:
                if not update.frames:
                    return
                frame_length = len(update.frames[0].frame) // 2
                mixed_acc = np.zeros(frame_length, dtype=np.int32)
                valid_frames = 0
                for frame_data in update.frames:
                    try:
                        source_samples = np.frombuffer(frame_data.frame, dtype=np.int16)
                        if len(source_samples) == frame_length:
                            mixed_acc += source_samples.astype(np.int32)
                            valid_frames += 1
                    except Exception:
                        continue
                if valid_frames == 0:
                    return
                mixed_acc //= valid_frames
                mixed_output = np.clip(mixed_acc, -32768, 32767).astype(np.int16)
                processed_output = process_audio_pipeline(mixed_output, config)
                processed_output = apply_echo_optimized(processed_output, echo_state, config)
                mixed_bytes = processed_output.tobytes()
                for chat_id in list(forward_chats_set):
                    try:
                        await call_py_obj.send_frame(chat_id, Device.MICROPHONE, mixed_bytes)
                    except Exception as e:
                        logger.debug(f"Dynamic send error to {chat_id}: {e}")
                        if "not found" in str(e).lower() or "invalid" in str(e).lower():
                            forward_chats_set.discard(chat_id)
                            logger.warning(f"Dynamic removed {chat_id}")
            except Exception as e:
                logger.error(f"Dynamic handler error: {e}")

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        logger.info(f"Dynamic client task cancelled for {client_data['user_id']}")
        raise

async def start_dynamic_client(user_id, api_id, api_hash, string_session, record_groupid):
    async with dynamic_clients_lock:
        if user_id in dynamic_clients:
            return False, "Already running"
    try:
        user_app_dyn = Client(
            f"dynamic_{user_id}",
            api_id=api_id,
            api_hash=api_hash,
            session_string=string_session
        )
        await user_app_dyn.start()
        call_py_dyn = PyTgCalls(user_app_dyn)
        await call_py_dyn.start()

        client_data = {
            'user_id': user_id,
            'user_app': user_app_dyn,
            'call_py': call_py_dyn,
            'record_source': int(record_groupid),
            'forward_chats': set(),
            'is_muted': False,
            'is_recording': False,
            'audio_config': audio_config.copy(),
            'lock': asyncio.Lock(),
            'task': None,
        }

        task = asyncio.create_task(dynamic_audio_forwarder(client_data))
        client_data['task'] = task

        async with dynamic_clients_lock:
            dynamic_clients[user_id] = client_data

        success, error = await join_call_safe(record_groupid, call_py_dyn, user_app_dyn)
        if success:
            await call_py_dyn.record(record_groupid, RecordStream(True, AUDIO_PARAMETERS))
            client_data['is_recording'] = True
            logger.info(f"Dynamic client {user_id} started, recording from {record_groupid}")
        else:
            logger.warning(f"Dynamic client {user_id} could not join: {error}")

        return True, "Dynamic client started"
    except Exception as e:
        logger.error(f"Failed to start dynamic client {user_id}: {e}")
        return False, str(e)

async def stop_dynamic_client(user_id):
    async with dynamic_clients_lock:
        client_data = dynamic_clients.pop(user_id, None)
    if client_data is None:
        return False, "Not found"
    try:
        if client_data['task']:
            client_data['task'].cancel()
            try:
                await client_data['task']
            except asyncio.CancelledError:
                pass
        for chat in list(client_data['forward_chats']):
            try:
                await client_data['call_py'].leave_call(chat)
            except Exception:
                pass
        try:
            await client_data['call_py'].leave_call(client_data['record_source'])
        except Exception:
            pass
        try:
            await client_data['call_py'].stop()
        except Exception:
            pass
        try:
            await client_data['user_app'].stop()
        except Exception:
            pass
        logger.info(f"Dynamic client {user_id} stopped")
        return True, "Stopped"
    except Exception as e:
        logger.error(f"Error stopping dynamic client {user_id}: {e}")
        return False, str(e)

async def dynamic_join(user_id, target_chat):
    async with dynamic_clients_lock:
        client_data = dynamic_clients.get(user_id)
    if client_data is None:
        return False, "No dynamic client for this user"
    if target_chat in client_data['forward_chats']:
        return False, "Already forwarding there"
    if target_chat == client_data['record_source']:
        return False, "Cannot forward to source"
    success, error = await join_call_safe(target_chat, client_data['call_py'], client_data['user_app'])
    if success:
        client_data['forward_chats'].add(target_chat)
        return True, "Joined"
    else:
        return False, error

# ==================== BOT COMMANDS ====================

@bot_app.on_message(pyro_filters.command("approve") & pyro_filters.user(OWNER_ID))
async def cmd_approve(client, message):
    try:
        user_id = None
        user_mention = None
        username = None
        first_name = None
        if message.reply_to_message:
            user = message.reply_to_message.from_user
            user_id = user.id
            user_mention = user.mention
            username = user.username or "No username"
            first_name = user.first_name or "Unknown"
        else:
            parts = message.text.split()
            if len(parts) < 2:
                await message.reply(
                    "❌ **Usage:** `/approve` (reply to a user)\n"
                    "Or: `/approve <user_id>`"
                )
                return
            try:
                user_id = int(parts[1])
                try:
                    user = await client.get_users(user_id)
                    user_mention = user.mention
                    username = user.username or "No username"
                    first_name = user.first_name or "Unknown"
                except Exception:
                    user_mention = f"User {user_id}"
                    username = "Unknown"
                    first_name = "Unknown"
            except ValueError:
                await message.reply("❌ **Invalid user ID format!**")
                return
        if user_id in approved_users:
            await message.reply(
                f"ℹ️ **User already approved!**\n\n"
                f"👤 {user_mention}\n"
                f"🆔 `{user_id}`"
            )
            return
        approved_users.add(user_id)
        save_state()
        approve_msg = f"""
✅ **User approved successfully!**

━━━━━━━━━━━━━━━━━━━━
👤 **Name:** {first_name} ({user_mention})
🆔 **ID:** `{user_id}`
📛 **Username:** @{username}
📊 **Status:** 🟢 Approved
━━━━━━━━━━━━━━━━━━━━

🎯 **They can now use the bot commands!**
"""
        await message.reply(approve_msg)
        logger.info(f"User {user_id} approved by owner")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{str(e)}`")

@bot_app.on_message(pyro_filters.command("disapprove") & pyro_filters.user(OWNER_ID))
async def cmd_disapprove(client, message):
    try:
        user_id = None
        user_mention = None
        if message.reply_to_message:
            user = message.reply_to_message.from_user
            user_id = user.id
            user_mention = user.mention
        else:
            parts = message.text.split()
            if len(parts) < 2:
                await message.reply(
                    "❌ **Usage:** `/disapprove` (reply to a user)\n"
                    "Or: `/disapprove <user_id>`"
                )
                return
            try:
                user_id = int(parts[1])
                try:
                    user = await client.get_users(user_id)
                    user_mention = user.mention
                except Exception:
                    user_mention = f"User {user_id}"
            except ValueError:
                await message.reply("❌ **Invalid user ID format!**")
                return
        if user_id not in approved_users:
            await message.reply(
                f"ℹ️ **User not found in approved list!**\n\n"
                f"👤 {user_mention}\n"
                f"🆔 `{user_id}`"
            )
            return
        approved_users.discard(user_id)
        save_state()
        disapprove_msg = f"""
❌ **User disapproved!**

━━━━━━━━━━━━━━━━━━━━
👤 **Name:** {user_mention}
🆔 **ID:** `{user_id}`
📊 **Status:** 🔴 Removed
━━━━━━━━━━━━━━━━━━━━

🚫 **They can no longer use the bot commands!**
"""
        await message.reply(disapprove_msg)
        logger.info(f"User {user_id} disapproved by owner")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{str(e)}`")

@bot_app.on_message(pyro_filters.command("userlist") & pyro_filters.user(OWNER_ID))
async def cmd_userlist(client, message):
    try:
        if not approved_users:
            await message.reply(
                "📭 **No users are approved yet.**\n\n"
                "Use `/approve` to add users."
            )
            return
        user_list = []
        total = len(approved_users)
        processed = 0
        for user_id in approved_users:
            processed += 1
            try:
                user = await client.get_users(user_id)
                username = f"@{user.username}" if user.username else "No username"
                mention = user.mention
                user_list.append(f"• {mention}\n  🆔 `{user_id}` | {username}")
            except Exception:
                user_list.append(f"• User `{user_id}` (⚠️ could not fetch)")
        response = f"""
📋 **Approved Users**

━━━━━━━━━━━━━━━━━━━━
📊 **Total:** {total} users
📈 **Processed:** {processed}/{total}
━━━━━━━━━━━━━━━━━━━━

"""
        response += "\n\n".join(user_list)
        response += f"\n\n━━━━━━━━━━━━━━━━━━━━\n✅ **Total:** {total}"
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="refresh_userlist"),
                InlineKeyboardButton("📊 Stats", callback_data="userlist_stats"),
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="back_start"),
            ]
        ])
        if len(response) > 4000:
            parts = [response[i:i+4000] for i in range(0, len(response), 4000)]
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    await message.reply(part, reply_markup=buttons)
                else:
                    await message.reply(part)
        else:
            await message.reply(response, reply_markup=buttons)
    except Exception as e:
        await message.reply(f"❌ **Error:** `{str(e)}`")

# ==================== BUTTON HELPERS ====================
class ButtonStyle:
    PRIMARY = "bg_primary"
    SUCCESS = "bg_success"
    DANGER = "bg_danger"

def styled_button(text: str, callback_data: str = None, url: str = None, style: str = ButtonStyle.PRIMARY) -> InlineKeyboardButton:
    if url:
        btn = InlineKeyboardButton(text=text, url=url)
    else:
        btn = InlineKeyboardButton(text=text, callback_data=callback_data)
    btn.__dict__["style"] = style
    return btn

def build_keyboard(buttons: list, row_width: int = 2) -> InlineKeyboardMarkup:
    rows, row = [], []
    for btn in buttons:
        if len(btn) == 4:
            text, callback, style, url = btn
            if style not in [ButtonStyle.PRIMARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER]:
                style = ButtonStyle.PRIMARY
            row.append(styled_button(text, callback_data=callback, url=url, style=style))
        elif len(btn) == 3:
            text, callback, style = btn
            if style not in [ButtonStyle.PRIMARY, ButtonStyle.SUCCESS, ButtonStyle.DANGER]:
                style = ButtonStyle.PRIMARY
            row.append(styled_button(text, callback_data=callback, style=style))
        else:
            text, callback = btn[0], btn[1]
            row.append(styled_button(text, callback_data=callback, style=ButtonStyle.PRIMARY))
        if len(row) >= row_width:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)

# ==================== CALLBACK HANDLERS ====================
@bot_app.on_callback_query(~pyro_filters.regex(r"^panel_"))
async def handle_callbacks(client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
    if user_id != OWNER_ID and user_id not in approved_users:
        await callback_query.answer("⛔ Unauthorized!", show_alert=True)
        return
    await callback_query.answer()
    if data == "refresh_userlist":
        if not approved_users:
            keyboard = build_keyboard([
                ("➕ Approve", "help_info", ButtonStyle.SUCCESS),
                ("🏠 Home", "back_start", ButtonStyle.PRIMARY)
            ], row_width=2)
            await callback_query.edit_message_text(
                "📭 **No users are approved yet.**",
                reply_markup=keyboard
            )
            return
        user_list = []
        for uid in approved_users:
            try:
                user = await client.get_users(uid)
                username = f"@{user.username}" if user.username else "No username"
                mention = user.mention
                user_list.append(f"• {mention}\n  🆔 `{uid}` | {username}")
            except Exception:
                user_list.append(f"• User `{uid}` (⚠️ could not fetch)")
        response = f"""
📋 **Approved Users** (🔄 Refreshed)
━━━━━━━━━━━━━━━━━━━━
📊 **Total:** {len(approved_users)} users
━━━━━━━━━━━━━━━━━━━━
"""
        response += "\n\n".join(user_list)
        response += f"\n\n━━━━━━━━━━━━━━━━━━━━\n✅ **Total:** {len(approved_users)}"
        keyboard = build_keyboard([
            ("🔄 Refresh", "refresh_userlist", ButtonStyle.PRIMARY),
            ("📊 Stats", "userlist_stats", ButtonStyle.PRIMARY),
            ("🏠 Home", "back_start", ButtonStyle.SUCCESS)
        ], row_width=2)
        await callback_query.edit_message_text(response, reply_markup=keyboard)
    elif data == "userlist_stats":
        total = len(approved_users)
        stats_msg = f"""
📊 **User Statistics**
━━━━━━━━━━━━━━━━━━━━
📋 **Total:** {total}
📈 **Status:** {'🟢 Active' if total > 0 else '🔴 Empty'}
━━━━━━━━━━━━━━━━━━━━
📊 **Progress**
{('█' * min(total, 10)) + ('░' * (10 - min(total, 10))) if total > 0 else '░░░░░░░░░░'}
⚡ **Usage:** {'Good' if total < 50 else 'Heavy' if total < 100 else 'Max'}
"""
        keyboard = build_keyboard([
            ("⬅️ Back", "back_userlist", ButtonStyle.PRIMARY)
        ], row_width=1)
        await callback_query.edit_message_text(stats_msg, reply_markup=keyboard)
    elif data == "back_userlist":
        if not approved_users:
            keyboard = build_keyboard([
                ("➕ Approve", "help_info", ButtonStyle.SUCCESS),
                ("🏠 Home", "back_start", ButtonStyle.PRIMARY)
            ], row_width=2)
            await callback_query.edit_message_text(
                "📭 **No users are approved**\n\nUse `/approve` to add",
                reply_markup=keyboard
            )
            return
        user_list = []
        for uid in approved_users:
            try:
                user = await client.get_users(uid)
                username = f"@{user.username}" if user.username else "No username"
                mention = user.mention
                user_list.append(f"• {mention}\n  🆔 `{uid}` | {username}")
            except Exception:
                user_list.append(f"• User `{uid}` (⚠️ could not fetch)")
        response = f"""
📋 **Approved Users**
━━━━━━━━━━━━━━━━━━━━
📊 **Total:** {len(approved_users)}
━━━━━━━━━━━━━━━━━━━━
"""
        response += "\n\n".join(user_list)
        response += f"\n\n━━━━━━━━━━━━━━━━━━━━\n✅ **Total:** {len(approved_users)}"
        keyboard = build_keyboard([
            ("🔄 Refresh", "refresh_userlist", ButtonStyle.PRIMARY),
            ("📊 Stats", "userlist_stats", ButtonStyle.PRIMARY),
            ("🏠 Home", "back_start", ButtonStyle.SUCCESS)
        ], row_width=2)
        await callback_query.edit_message_text(response, reply_markup=keyboard)
    elif data == "help_info":
        help_text = """
📖 **Help Menu**
━━━━━━━━━━━━━━━━━━━━
🎵 **Audio Controls**
/record - Start recording
/join <id> - Forward to chat
/rejoin - Reconnect all
/leave <id> - Stop forwarding
/leaveall - Stop all
/leaverecord - Leave source
/mute - Mute
/unmute - Unmute
━━━━━━━━━━━━━━━━━━━━
🎛️ **Effects** (no limits!)
/level <0-∞> - Volume
/bass <0-∞> - Bass
/treble <0-∞> - Treble
/gain <0-∞> - Gain
/overdrive <0-∞> - Distortion/Gain
/echo <delay/decay/off> - Echo
/effects - Show effects
/reset - Reset all
━━━━━━━━━━━━━━━━━━━━
📊 **Utility**
/ping - Check bot
/stats - Bot stats
/status - System status
/list - Forward list
/id - Get chat ID
/panel - Control panel
━━━━━━━━━━━━━━━━━━━━
👤 **User Mgmt** (owner)
/approve - Add user
/disapprove - Remove
/userlist - List users
/setrecordgroup <id> - Change source
/restart - Restart bot
"""
        keyboard = build_keyboard([
            ("🏠 Home", "back_start", ButtonStyle.SUCCESS)
        ], row_width=1)
        await callback_query.edit_message_text(
            help_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
    elif data == "back_start":
        user = callback_query.from_user
        first_name = escape_markdown(user.first_name or "User")
        welcome_text = f"""
✨ **Welcome to Audio Forwarder** ✨
━━━━━━━━━━━━━━━━━━━━
👋 **Hello, {first_name}!**
━━━━━━━━━━━━━━━━━━━━
🎵 **Real-time Audio Forwarding**
📊 **System:** ██████████ 100% ✅
⚡ **Features:**
• Real-time forwarding
• Advanced effects
• Volume control
• Multi-chat forwarding
• Bass/Treble boost
• Unlimited echo
• Distortion/Gain effect
━━━━━━━━━━━━━━━━━━━━
📖 **Use /help for commands**
"""
        keyboard = build_keyboard([
            ("👤 Owner", "owner", ButtonStyle.PRIMARY),
            ("📖 Help", "help_info", ButtonStyle.PRIMARY)
        ], row_width=2)
        try:
            await callback_query.edit_message_text(
                welcome_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard
            )
        except Exception:
            pass
    elif data == "owner":
        keyboard = build_keyboard([
            ("🏠 Home", "back_start", ButtonStyle.SUCCESS)
        ], row_width=1)
        await callback_query.edit_message_text(
            "👤 **Owner**\n\n"
            "📖 @llNOBI_XDll\n"
            "📱 [Telegram](t.me/RAJAJIBOLOJI)",
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

# ===== START COMMAND =====
@bot_app.on_message(pyro_filters.command("start"))
async def cmd_start(client, message):
    user_id = message.from_user.id if message.from_user else None
    if user_id != OWNER_ID and user_id not in approved_users:
        logger.info(f"Unauthorized /start from {user_id}")
        return
    user = message.from_user
    first_name = escape_markdown(user.first_name or "User")
    welcome_text = f"""
✨ **Welcome to Audio Forwarder** ✨
━━━━━━━━━━━━━━━━━━━━
👋 **Hello, {first_name}!**
━━━━━━━━━━━━━━━━━━━━
🎵 **Real-time Audio Forwarding**
📊 **System:** ██████████ 100% ✅
⚡ **Features:**
• Real-time forwarding
• Advanced effects
• Volume control
• Multi-chat forwarding
• Bass/Treble boost
• Unlimited echo
• Distortion/Gain effect
━━━━━━━━━━━━━━━━━━━━
📖 **Use /help for commands**
"""
    keyboard = build_keyboard([
        ("👤 Owner", "owner", ButtonStyle.PRIMARY),
        ("📖 Help", "help_info", ButtonStyle.PRIMARY)
    ], row_width=2)
    await message.reply(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

# ===== HELP COMMAND =====
@bot_app.on_message(pyro_filters.command("help") & authorized_only())
async def cmd_help(client, message):
    help_text = """
📖 **Help Menu**
━━━━━━━━━━━━━━━━━━━━
🎵 **Audio Controls**
/record - Start recording
/join <id> - Forward to chat
/rejoin - Reconnect all
/leave <id> - Stop forwarding
/leaveall - Stop all
/leaverecord - Leave source
/mute - Mute
/unmute - Unmute
━━━━━━━━━━━━━━━━━━━━
🎛️ **Effects** (no limits!)
/level <0-∞> - Volume
/bass <0-∞> - Bass
/treble <0-∞> - Treble
/gain <0-∞> - Gain
/overdrive <0-∞> - Distortion/Gain
/echo <delay/decay/off> - Echo
/effects - Show effects
/reset - Reset all
━━━━━━━━━━━━━━━━━━━━
📊 **Utility**
/ping - Check bot
/stats - Bot stats
/status - System status
/list - Forward list
/id - Get chat ID
/panel - Control panel
━━━━━━━━━━━━━━━━━━━━
👤 **User Mgmt** (owner)
/approve - Add user
/disapprove - Remove
/userlist - List users
/setrecordgroup <id> - Change source
/restart - Restart bot
"""
    keyboard = build_keyboard([
        ("🏠 Home", "back_start", ButtonStyle.SUCCESS),
        ("👤 Owner", "owner", ButtonStyle.PRIMARY)
    ], row_width=2)
    await message.reply(
        help_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

# ===== UTILITY COMMANDS =====
@bot_app.on_message(pyro_filters.command("id") & pyro_filters.user(OWNER_ID))
async def cmd_id(client, message):
    chat_type = "Group" if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) else "Private"
    id_msg = f"""
🆔 **Chat Information**

━━━━━━━━━━━━━━━━━━━━
🆔 **Chat ID:** `{message.chat.id}`
📊 **Type:** {chat_type}
📛 **Title:** {message.chat.title or 'Private Chat'}
━━━━━━━━━━━━━━━━━━━━

✅ **Copy this ID for use in commands**
"""
    await message.reply(id_msg)

@bot_app.on_message(pyro_filters.command("ping") & authorized_only())
async def cmd_ping(client, message):
    start = time.time()
    msg = await message.reply("🏓 Pinging...")
    end = time.time()
    ping = round((end - start) * 1000)
    await msg.edit_text(
        f"🏓 **Pong!**\n\n"
        f"⏱️ **Ping:** `{ping}ms`\n"
        f"⏳ **Uptime:** `{get_uptime()}`"
    )

@bot_app.on_message(pyro_filters.command("stats") & pyro_filters.user(OWNER_ID))
async def cmd_stats(client, message):
    stats_msg = f"""
📊 **Bot Statistics**

━━━━━━━━━━━━━━━━━━━━
⏳ **Uptime:** `{get_uptime()}`
📋 **Approved Users:** {len(approved_users)}
🔗 **Active Forwards:** {len(forward_chats)}
📡 **Source:** `{RECORD_SOURCE}`
🔇 **Audio:** {'🔇 Muted' if is_muted else '🔊 Live'}
🎵 **Recording:** {'🟢 On' if is_recording else '🔴 Off'}
━━━━━━━━━━━━━━━━━━━━

🎛️ **Active Effects**
• Volume: `{audio_config['volume']}%`
• Bass: `{audio_config['bass']}`
• Treble: `{audio_config['treble']}`
• Gain: `{audio_config['gain']}`
• Overdrive: `{audio_config['overdrive']}`
• Echo Delay: `{audio_config['echo_delay']}ms`
• Echo Decay: `{audio_config['echo_decay']}`
"""
    await message.reply(stats_msg)

# ===== AUDIO CONTROLS =====
@bot_app.on_message(pyro_filters.command("record") & authorized_only())
async def cmd_record(client, message):
    global is_recording
    if is_recording:
        await message.reply(
            "⚠️ **Already recording!**\n\n"
            f"📡 **Source:** `{RECORD_SOURCE}`\n"
            f"🔗 **Forwarding:** {len(forward_chats)} chats"
        )
        return
    try:
        status_msg = await message.reply(
            "🔄 **Starting recording...**\n\n"
            "📡 Connecting to source..."
        )
        success, error = await join_call_safe(RECORD_SOURCE)
        if success:
            await call_py.record(RECORD_SOURCE, RecordStream(True, AUDIO_PARAMETERS))
            is_recording = True
            record_msg = f"""
✅ **Recording started successfully!**

━━━━━━━━━━━━━━━━━━━━
📡 **Source:** `{RECORD_SOURCE}`
🔗 **Forwarding:** {len(forward_chats)} chats
📊 **Status:** 🟢 Live
━━━━━━━━━━━━━━━━━━━━

🎵 **Audio is now flowing!**
"""
            await status_msg.edit_text(record_msg)
            logger.info("Recording started")
        else:
            await status_msg.edit_text(
                f"❌ **Failed to start recording!**\n\n"
                f"⚠️ **Error:** `{error}`\n\n"
                f"💡 Make sure chat is active"
            )
    except Exception as e:
        await message.reply(f"❌ **Error occurred!**\n\n⚠️ `{str(e)}`")

@bot_app.on_message(pyro_filters.command("join") & authorized_only())
async def cmd_join(client, message):
    parts = message.text.split()
    if len(parts) < 2:
        join_help = """
❌ **Usage:** `/join <chat_id>`

━━━━━━━━━━━━━━━━━━━━
📖 **Example:**
`/join -1003929100976`

💡 **How to get chat ID:**
• Forward a message to @statusrobot
• Or use `/id` in the chat (owner)
━━━━━━━━━━━━━━━━━━━━

⚠️ **Note:** Chat ID must be numeric
"""
        await message.reply(join_help)
        return
    chat_id_str = re.sub(r'[^\d-]', '', parts[1])
    try:
        chat_id = int(chat_id_str)
        if chat_id in forward_chats:
            await message.reply(
                f"⚠️ **Already forwarding to this chat!**\n\n"
                f"🎯 `{chat_id}`\n"
                f"🔗 **Total:** {len(forward_chats)} chats"
            )
            return
        if chat_id == RECORD_SOURCE:
            await message.reply(
                "⚠️ **Cannot forward to source chat!**\n\n"
                f"📡 **Source:** `{RECORD_SOURCE}`\n"
                "💡 Use a different chat for forwarding"
            )
            return
        status_msg = await message.reply(
            f"🔄 **Connecting to `{chat_id}`...**\n\n"
            "📡 Establishing audio forwarding..."
        )
        success, error = await join_call_safe(chat_id)
        if success:
            forward_chats.add(chat_id)
            join_msg = f"""
✅ **Forwarding started successfully!**

━━━━━━━━━━━━━━━━━━━━
🎯 **Target:** `{chat_id}`
🔗 **Total connections:** {len(forward_chats)}
📊 **Status:** 🟢 Connected
━━━━━━━━━━━━━━━━━━━━

🎵 **Audio is now flowing to this chat!**
"""
            await status_msg.edit_text(join_msg)
            logger.info(f"Joined {chat_id}")
        else:
            await status_msg.edit_text(
                f"❌ **Failed to join chat!**\n\n"
                f"⚠️ **Error:** `{error}`\n\n"
                f"💡 **Troubleshooting:**\n"
                f"• Ensure chat is active\n"
                f"• Check if bot is admin\n"
                f"• Verify chat ID is correct"
            )
    except ValueError:
        await message.reply(
            f"❌ **Invalid chat ID format!**\n\n"
            f"📖 **You entered:** `{parts[1]}`\n"
            f"💡 Use a numeric ID (e.g., `-1003929100976`)"
        )
    except Exception as e:
        await message.reply(f"❌ **Error occurred!**\n\n⚠️ `{str(e)}`")

# ===== REJOIN COMMAND =====
@bot_app.on_message(pyro_filters.command("rejoin") & authorized_only())
async def cmd_rejoin(client, message):
    global is_recording
    status_msg = await message.reply(
        "🔄 **Reconnecting all chats...**\n\n"
        "📡 Disconnecting and reconnecting all connections..."
    )
    rejoin_results = {
        "source": {"status": "❌", "error": None},
        "forwards": {"total": 0, "success": 0, "failed": 0, "errors": []}
    }
    was_recording = is_recording
    source_chat = RECORD_SOURCE
    if was_recording:
        try:
            await call_py.leave_call(source_chat)
            logger.info(f"Left source {source_chat} for rejoin")
            await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"Could not leave source: {e}")
        try:
            success, error = await join_call_safe(source_chat)
            if success:
                await call_py.record(source_chat, RecordStream(True, AUDIO_PARAMETERS))
                is_recording = True
                rejoin_results["source"]["status"] = "✅"
                logger.info(f"Reconnected source {source_chat} successfully")
            else:
                is_recording = False
                rejoin_results["source"]["status"] = "❌"
                rejoin_results["source"]["error"] = error
                logger.error(f"Failed to reconnect source: {error}")
        except Exception as e:
            is_recording = False
            rejoin_results["source"]["status"] = "❌"
            rejoin_results["source"]["error"] = str(e)
            logger.error(f"Error reconnecting source: {e}")
    else:
        rejoin_results["source"]["status"] = "⏸️"
        rejoin_results["source"]["error"] = "Not recording"

    forward_list = list(forward_chats)
    rejoin_results["forwards"]["total"] = len(forward_list)
    if forward_list:
        for chat_id in forward_list:
            try:
                await call_py.leave_call(chat_id)
                logger.info(f"Left forward chat {chat_id} for rejoin")
            except Exception as e:
                logger.debug(f"Could not leave {chat_id}: {e}")
        await asyncio.sleep(1.5)
        for chat_id in forward_list:
            try:
                success, error = await join_call_safe(chat_id)
                if success:
                    rejoin_results["forwards"]["success"] += 1
                    logger.info(f"Reconnected forward chat {chat_id} successfully")
                else:
                    rejoin_results["forwards"]["failed"] += 1
                    rejoin_results["forwards"]["errors"].append(f"`{chat_id}`: {error[:50]}")
                    logger.error(f"Failed to reconnect {chat_id}: {error}")
                    forward_chats.discard(chat_id)
            except Exception as e:
                rejoin_results["forwards"]["failed"] += 1
                rejoin_results["forwards"]["errors"].append(f"`{chat_id}`: {str(e)[:50]}")
                forward_chats.discard(chat_id)
                logger.error(f"Error reconnecting {chat_id}: {e}")
    else:
        rejoin_results["forwards"]["status"] = "⏸️"
        rejoin_results["forwards"]["error"] = "No forward chats"

    response = f"""
🔄 **Reconnect Complete!**

━━━━━━━━━━━━━━━━━━━━
📡 **Source Chat:** `{source_chat}`
{'✅ Reconnected' if rejoin_results['source']['status'] == '✅' else '❌ Failed' if rejoin_results['source']['status'] == '❌' else '⏸️ Not active'}
"""
    if rejoin_results['source']['error']:
        response += f"⚠️ **Error:** `{rejoin_results['source']['error']}`\n"
    response += f"""
━━━━━━━━━━━━━━━━━━━━
🔗 **Forward Status:**
• **Total:** {rejoin_results['forwards']['total']}
• **✅ Success:** {rejoin_results['forwards']['success']}
• **❌ Failed:** {rejoin_results['forwards']['failed']}
"""
    if rejoin_results['forwards']['errors']:
        response += "\n⚠️ **Failed chats:**\n"
        for err in rejoin_results['forwards']['errors'][:5]:
            response += f"• {err}\n"
    response += f"""
━━━━━━━━━━━━━━━━━━━━
📊 **Final Status:**
• **Recording:** {'🟢 Active' if is_recording else '🔴 Inactive'}
• **Forwarding:** {len(forward_chats)} chats
• **Audio:** {'🔇 Muted' if is_muted else '🔊 Live'}
"""
    await status_msg.edit_text(response)

# ===== LEAVE COMMANDS =====
@bot_app.on_message(pyro_filters.command("leaverecord") & authorized_only())
async def cmd_leaverecord(client, message):
    global is_recording
    try:
        status_msg = await message.reply("🔄 **Leaving source chat...**")
        await call_py.leave_call(RECORD_SOURCE)
        is_recording = False
        leave_msg = f"""
✅ **Left source chat successfully!**

━━━━━━━━━━━━━━━━━━━━
📡 **Source:** `{RECORD_SOURCE}`
📊 **Status:** 🔴 Disconnected
🔗 **Active Forwarding:** {len(forward_chats)}
━━━━━━━━━━━━━━━━━━━━

🎵 **Recording has been stopped!**
"""
        await status_msg.edit_text(leave_msg)
        logger.info("Left record chat")
    except Exception as e:
        await message.reply(f"❌ **Error occurred!**\n\n⚠️ `{str(e)}`")

@bot_app.on_message(pyro_filters.command("leave") & authorized_only())
async def cmd_leave(client, message):
    parts = message.text.split()
    if len(parts) >= 2:
        chat_id_str = re.sub(r'[^\d-]', '', parts[1])
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            await message.reply(
                f"❌ **Invalid chat ID!**\n\n"
                f"📖 **You entered:** `{parts[1]}`\n"
                f"💡 Use a numeric ID (e.g., `-1003929100976`)"
            )
            return
        if chat_id not in forward_chats:
            await message.reply(
                f"⚠️ **Not forwarding to this chat!**\n\n🎯 `{chat_id}`"
            )
            return
        try:
            status_msg = await message.reply(f"🔄 **Leaving `{chat_id}`...**")
            await call_py.leave_call(chat_id)
            forward_chats.discard(chat_id)
            leave_msg = f"""
✅ **Left chat successfully!**

━━━━━━━━━━━━━━━━━━━━
🎯 **Chat:** `{chat_id}`
🔗 **Remaining Forwarding:** {len(forward_chats)}
📊 **Status:** 🔴 Disconnected
━━━━━━━━━━━━━━━━━━━━
"""
            await status_msg.edit_text(leave_msg)
            logger.info(f"Left chat {chat_id}")
        except Exception as e:
            logger.error(f"cmd_leave error for {chat_id}: {e}")
            await message.reply(f"❌ **Error leaving chat!**\n\n⚠️ `{str(e)}`")
    else:
        if not forward_chats:
            await message.reply(
                "📭 **No chats to leave!**\n\n💡 Use `/join` to start forwarding"
            )
            return
        count = len(forward_chats)
        status_msg = await message.reply(f"🔄 **Leaving all {count} chats...**")
        left_count = 0
        for cid in list(forward_chats):
            try:
                await call_py.leave_call(cid)
                left_count += 1
            except Exception:
                pass
        forward_chats.clear()
        leave_msg = f"""
✅ **Left all chats successfully!**

━━━━━━━━━━━━━━━━━━━━
🔗 **Total Left:** {left_count}/{count} chats
📊 **Status:** 🔴 All disconnected
📡 **Source:** {'🟢 Active' if is_recording else '🔴 Inactive'}
━━━━━━━━━━━━━━━━━━━━
"""
        await status_msg.edit_text(leave_msg)

@bot_app.on_message(pyro_filters.command("leaveall") & authorized_only())
async def cmd_leaveall(client, message):
    global is_recording
    total_forward = len(forward_chats)
    status_msg = await message.reply(
        "🔄 **Disconnecting all chats...**\n\n📡 Clearing up all connections..."
    )
    try:
        await call_py.leave_call(RECORD_SOURCE)
        is_recording = False
    except Exception:
        pass
    left_count = 0
    for cid in list(forward_chats):
        try:
            await call_py.leave_call(cid)
            left_count += 1
        except Exception:
            pass
    forward_chats.clear()
    leaveall_msg = f"""
✅ **All chats disconnected successfully!**

━━━━━━━━━━━━━━━━━━━━
📡 **Source:** ✅ Left
🔗 **Forwarding:** {left_count}/{total_forward} left
📊 **Status:** 🔴 All disconnected
━━━━━━━━━━━━━━━━━━━━
"""
    await status_msg.edit_text(leaveall_msg)
    logger.info("Left all chats")

# ===== MUTE/UNMUTE COMMANDS =====
@bot_app.on_message(pyro_filters.command("mute") & authorized_only())
async def cmd_mute(client, message):
    global is_muted
    if is_muted:
        await message.reply("🔇 **Already muted!**")
        return
    is_muted = True
    await message.reply("🔇 **Audio muted successfully!**")

@bot_app.on_message(pyro_filters.command("unmute") & authorized_only())
async def cmd_unmute(client, message):
    global is_muted
    if not is_muted:
        await message.reply("🔊 **Not muted!**")
        return
    is_muted = False
    await message.reply("🔊 **Audio unmuted successfully!**")

# ===== EFFECTS COMMANDS =====
@bot_app.on_message(pyro_filters.command("level") & authorized_only())
async def cmd_level(client, message):
    parts = message.text.split()
    current = audio_config['volume']
    if len(parts) < 2:
        bar = create_progress_bar(min(current, 200), 200)
        await message.reply(
            f"🔊 **Volume Control**\n\n"
            f"📊 **Current:** `{current}%`\n"
            f"📈 {bar}\n\n"
            f"💡 **Usage:** `/level <0-∞>`"
        )
        return
    try:
        level = int(parts[1])
        audio_config['volume'] = max(0, level)
        save_state()
        bar = create_progress_bar(min(abs(level), 200), 200)
        await message.reply(
            f"✅ **Volume updated!**\n\n"
            f"📊 **New:** `{level}%`\n"
            f"📈 {bar}"
        )
    except ValueError:
        await message.reply(f"❌ **Invalid value:** `{parts[1]}`")

@bot_app.on_message(pyro_filters.command("bass") & authorized_only())
async def cmd_bass(client, message):
    parts = message.text.split()
    current = audio_config['bass']
    if len(parts) < 2:
        bar = create_progress_bar(min(current, 60), 60)
        await message.reply(
            f"🎸 **Bass Boost**\n\n"
            f"📊 **Current:** `{current}`\n"
            f"📈 {bar}\n\n"
            f"💡 **Usage:** `/bass <0-∞>`"
        )
        return
    try:
        level = int(parts[1])
        audio_config['bass'] = max(0, level)
        audio_config['highpass'] = level > 0
        save_state()
        bar = create_progress_bar(min(abs(level), 60), 60)
        await message.reply(
            f"✅ **Bass updated!**\n\n"
            f"📊 **New:** `{level}`\n"
            f"📈 {bar}"
        )
    except ValueError:
        await message.reply(f"❌ **Invalid value:** `{parts[1]}`")

@bot_app.on_message(pyro_filters.command("treble") & authorized_only())
async def cmd_treble(client, message):
    parts = message.text.split()
    current = audio_config['treble']
    if len(parts) < 2:
        bar = create_progress_bar(min(current, 60), 60)
        await message.reply(
            f"🎵 **Treble Boost**\n\n"
            f"📊 **Current:** `{current}`\n"
            f"📈 {bar}\n\n"
            f"💡 **Usage:** `/treble <0-∞>`"
        )
        return
    try:
        level = int(parts[1])
        audio_config['treble'] = max(0, level)
        save_state()
        bar = create_progress_bar(min(abs(level), 60), 60)
        await message.reply(
            f"✅ **Treble updated!**\n\n"
            f"📊 **New:** `{level}`\n"
            f"📈 {bar}"
        )
    except ValueError:
        await message.reply(f"❌ **Invalid value:** `{parts[1]}`")

@bot_app.on_message(pyro_filters.command("gain") & authorized_only())
async def cmd_gain(client, message):
    parts = message.text.split()
    current = audio_config['gain']
    if len(parts) < 2:
        bar = create_progress_bar(min(current, 60), 60)
        await message.reply(
            f"📈 **Gain Control**\n\n"
            f"📊 **Current:** `{current}`\n"
            f"📈 {bar}\n\n"
            f"💡 **Usage:** `/gain <0-∞>`"
        )
        return
    try:
        level = int(parts[1])
        audio_config['gain'] = max(0, level)
        save_state()
        bar = create_progress_bar(min(abs(level), 60), 60)
        await message.reply(
            f"✅ **Gain updated!**\n\n"
            f"📊 **New:** `{level}`\n"
            f"📈 {bar}"
        )
    except ValueError:
        await message.reply(f"❌ **Invalid value:** `{parts[1]}`")

@bot_app.on_message(pyro_filters.command("overdrive") & authorized_only())
async def cmd_overdrive(client, message):
    parts = message.text.split()
    current = audio_config.get('overdrive', 0)
    gain = audio_config.get('overdrive_gain', 1.0)
    tone = audio_config.get('overdrive_tone', 0.5)
    
    if len(parts) < 2:
        bar = create_progress_bar(min(current, 100), 100)
        await message.reply(
            f"🔥 **Overdrive/Distortion**\n\n"
            f"📊 **Current:** `{current}/100`\n"
            f"📈 {bar}\n"
            f"📊 **Gain:** `{gain}x` | **Tone:** `{tone}`\n\n"
            f"💡 **Usage:**\n"
            f"`/overdrive <0-100>`\n"
            f"`/overdrive gain <1.0-10.0>`\n"
            f"`/overdrive tone <0.0-1.0>`\n"
            f"`/overdrive off`"
        )
        return
    
    action = parts[1].lower()
    
    if action == "off":
        audio_config['overdrive'] = 0
        save_state()
        await message.reply("🔇 **Overdrive disabled!**")
        return
    
    if len(parts) < 3 and action in ["gain", "tone"]:
        await message.reply(f"❌ **Usage:** `/overdrive {action} <value>`")
        return
    
    try:
        if action == "gain":
            val = float(parts[2])
            audio_config['overdrive_gain'] = max(0.5, min(10.0, val))
            save_state()
            await message.reply(f"✅ **Overdrive gain set to:** `{audio_config['overdrive_gain']}x`")
        elif action == "tone":
            val = float(parts[2])
            audio_config['overdrive_tone'] = max(0.0, min(1.0, val))
            save_state()
            await message.reply(f"✅ **Overdrive tone set to:** `{audio_config['overdrive_tone']}`")
        else:
            value = int(parts[1])
            audio_config['overdrive'] = max(0, value)
            save_state()
            bar = create_progress_bar(min(value, 100), 100)
            await message.reply(
                f"✅ **Overdrive updated!**\n\n"
                f"📊 **New:** `{value}/100`\n"
                f"📈 {bar}"
            )
    except ValueError:
        await message.reply("❌ **Invalid value provided!**")

@bot_app.on_message(pyro_filters.command("echo") & authorized_only())
async def cmd_echo(client, message):
    parts = message.text.split()
    current_delay = audio_config.get('echo_delay', 70)
    current_decay = audio_config.get('echo_decay', 0.32)
    
    if len(parts) < 2:
        await message.reply(
            f"🎵 **Echo Controls**\n\n"
            f"📊 **Current Settings:**\n"
            f"• **Delay:** `{current_delay}ms`\n"
            f"• **Decay:** `{current_decay}`\n\n"
            f"💡 **Usage:**\n"
            f"`/echo delay <1-5000>`\n"
            f"`/echo decay <0.01-5.0>`\n"
            f"`/echo off`"
        )
        return
    
    action = parts[1].lower()
    
    if action == "off":
        audio_config['echo_delay'] = 0
        audio_config['echo_decay'] = 0
        save_state()
        await message.reply("🔇 **Echo disabled!**")
        return
    
    if len(parts) < 3:
        await message.reply(f"❌ **Usage:** `/echo {action} <value>`")
        return
    
    try:
        value = float(parts[2])
        if action == "delay":
            audio_config['echo_delay'] = max(1, value)
            await message.reply(f"✅ **Echo delay set to:** `{audio_config['echo_delay']}ms`")
        elif action == "decay":
            audio_config['echo_decay'] = max(0.01, value)
            await message.reply(f"✅ **Echo decay set to:** `{audio_config['echo_decay']}`")
        else:
            await message.reply(f"❌ **Unknown echo option:** `{action}`")
            return
        save_state()
    except ValueError:
        await message.reply(f"❌ **Invalid value:** `{parts[2]}`")

@bot_app.on_message(pyro_filters.command("effects") & authorized_only())
async def cmd_effects(client, message):
    config = audio_config
    scipy_status = "✅ Advanced available" if SCIPY_AVAILABLE else "❌ Basic only"
    vol_bar = create_progress_bar(min(config['volume'], 200), 200)
    bass_bar = create_progress_bar(min(config['bass'], 60), 60)
    treble_bar = create_progress_bar(min(config['treble'], 60), 60)
    gain_bar = create_progress_bar(min(config['gain'], 60), 60)
    od_bar = create_progress_bar(min(config.get('overdrive', 0), 100), 100)
    effects_msg = f"""
🎛️ **Audio Effects Dashboard**

━━━━━━━━━━━━━━━━━━━━
🔊 **Volume:** `{config['volume']}%` {vol_bar}
🎸 **Bass:** `{config['bass']}` {bass_bar}
🎵 **Treble:** `{config['treble']}` {treble_bar}
📈 **Gain:** `{config['gain']}` {gain_bar}
🔥 **Overdrive:** `{config.get('overdrive', 0)}/100` {od_bar}

🔄 **Echo:**
• Delay: `{config['echo_delay']}ms`
• Decay: `{config['echo_decay']}`

🔬 **Engine:** {scipy_status}
"""
    await message.reply(effects_msg)

@bot_app.on_message(pyro_filters.command("reset") & authorized_only())
async def cmd_reset(client, message):
    global audio_config
    audio_config = {
        'volume': 100,
        'bass': 0,
        'treble': 0,
        'gain': 0,
        'compressor': True,
        'limiter': True,
        'highpass': False,
        'lowpass': False,
        'echo_delay': 70,
        'echo_decay': 0.32,
        'freq_shift': 2.0,
        'pre_gain': 1.0,
        'overdrive': 0,
        'overdrive_gain': 1.0,
        'overdrive_tone': 0.5,
        'reverb_room': 0,
        'reverb_damp': 0.5,
        'stereo_width': 0,
    }
    _filter_cache.clear()
    save_state()
    await message.reply("✅ **All audio effects reset to factory settings!**")

@bot_app.on_message(pyro_filters.command("status") & authorized_only())
async def cmd_status(client, message):
    recording_status = "🟢 Active" if is_recording else "🔴 Inactive"
    audio_status = "🔇 Muted" if is_muted else "🔊 Live"
    status_msg = f"""
📊 **Bot Status Dashboard**

━━━━━━━━━━━━━━━━━━━━
🔴 **Recording:** {recording_status}
📡 **Source:** `{RECORD_SOURCE}`
🔊 **Audio:** {audio_status}
🔗 **Forwarding:** {len(forward_chats)} chats
⏳ **Uptime:** `{get_uptime()}`
"""
    await message.reply(status_msg)

@bot_app.on_message(pyro_filters.command("list") & authorized_only())
async def cmd_list(client, message):
    if not forward_chats:
        await message.reply("📭 **No active forwarding**")
        return
    chat_list = [f"`{idx}.` `{cid}`" for idx, cid in enumerate(forward_chats, 1)]
    list_msg = f"🔗 **Forwarding Chats List ({len(forward_chats)})**\n\n" + "\n".join(chat_list)
    await message.reply(list_msg)

# ===== PANEL & DYNAMIC COMMANDS =====
@bot_app.on_message(pyro_filters.command("panel") & authorized_only())
async def cmd_panel(client, message):
    recording_status = "🟢 Active" if is_recording else "🔴 Inactive"
    audio_status = "🔇 Muted" if is_muted else "🔊 Live"
    panel_text = f"""
🎛️ **Control Panel**

━━━━━━━━━━━━━━━━━━━━
📊 **Status:** Recording {recording_status} | Audio {audio_status}
🔗 **Forwarding:** {len(forward_chats)} chats | Uptime `{get_uptime()}`
"""
    keyboard = build_keyboard([
        ("🔇 Mute", "panel_mute", ButtonStyle.DANGER),
        ("🔊 Unmute", "panel_unmute", ButtonStyle.SUCCESS),
        ("🔄 Reset", "panel_reset", ButtonStyle.DANGER),
        ("❌ Close", "panel_close", ButtonStyle.DANGER),
    ], row_width=2)
    await message.reply(panel_text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

@bot_app.on_callback_query(pyro_filters.regex(r"^panel_"))
async def panel_callbacks(client, callback_query: CallbackQuery):
    global is_muted, audio_config
    data = callback_query.data
    user_id = callback_query.from_user.id
    if user_id != OWNER_ID and user_id not in approved_users:
        await callback_query.answer("⛔ Unauthorized!", show_alert=True)
        return
    await callback_query.answer()
    if data == "panel_mute":
        is_muted = True
        await callback_query.answer("🔇 Audio muted!", show_alert=True)
    elif data == "panel_unmute":
        is_muted = False
        await callback_query.answer("🔊 Audio unmuted!", show_alert=True)
    elif data == "panel_reset":
        audio_config['volume'] = 100
        audio_config['bass'] = 0
        audio_config['treble'] = 0
        audio_config['gain'] = 0
        audio_config['overdrive'] = 0
        _filter_cache.clear()
        save_state()
        await callback_query.answer("🔄 All effects reset!", show_alert=True)
    elif data == "panel_close":
        try:
            await callback_query.message.delete()
        except Exception:
            pass

@bot_app.on_message(pyro_filters.command("set") & authorized_only())
async def cmd_set(client, message):
    parts = message.text.split()
    if len(parts) < 5:
        await message.reply("❌ **Usage:** `/set <api_id> <api_hash> <string_session> <record_groupid>`")
        return
    try:
        success, msg = await start_dynamic_client(message.from_user.id, int(parts[1]), parts[2], parts[3], int(parts[4]))
        await message.reply(f"Status: `{msg}`")
    except ValueError:
        await message.reply("❌ **Invalid ID values provided.**")

@bot_app.on_message(pyro_filters.command("djoin") & authorized_only())
async def cmd_djoin(client, message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("❌ **Usage:** `/djoin <target_group_id>`")
        return
    try:
        success, msg = await dynamic_join(message.from_user.id, int(parts[1]))
        await message.reply(f"Status: `{msg}`")
    except ValueError:
        await message.reply("❌ **Invalid target ID.**")

@bot_app.on_message(pyro_filters.command("dstop") & authorized_only())
async def cmd_dstop(client, message):
    success, msg = await stop_dynamic_client(message.from_user.id)
    await message.reply(f"Status: `{msg}`")

@bot_app.on_message(pyro_filters.command("setrecordgroup") & pyro_filters.user(OWNER_ID))
async def cmd_setrecordgroup(client, message):
    global RECORD_SOURCE, is_recording
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply(f"❌ **Usage:** `/setrecordgroup <chat_id>`\nSource: `{RECORD_SOURCE}`")
        return
    try:
        new_source = int(re.sub(r'[^\d-]', '', parts[1]))
        if is_recording:
            try:
                await call_py.leave_call(RECORD_SOURCE)
                is_recording = False
            except Exception:
                pass
        RECORD_SOURCE = new_source
        save_state()
        await message.reply(f"✅ **Source changed to:** `{RECORD_SOURCE}`")
    except ValueError:
        await message.reply("❌ **Invalid chat ID.**")

@bot_app.on_message(pyro_filters.command("joinlink") & authorized_only())
async def cmd_joinlink(client, message):
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply("❌ **Usage:** `/joinlink <invite_link>`")
        return
    try:
        chat = await user_app.join_chat(parts[1].strip())
        await message.reply(f"✅ **Joined!** Chat: `{chat.id}`")
    except Exception as e:
        await message.reply(f"❌ **Error:** `{str(e)}`")

@bot_app.on_message(pyro_filters.command("restart") & authorized_only())
async def cmd_restart(client, message):
    await message.reply("🔄 **Restarting process...**")
    save_state()
    os.system("python bot.py")

# ==================== MAIN RUNNER ====================
if __name__ == "__main__":
    load_state()
    print("🎵 Audio Forwarder v7 - Running...")
    try:
        bot_app.start()
        call_py.start()
        print("✅ Online! System active.")
        idle()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    except Exception as e:
        print(f"❌ Fatal error: {e}")
    finally:
        try:
            for chat in list(forward_chats):
                try:
                    call_py.leave_call(chat)
                except Exception:
                    pass
            try:
                call_py.leave_call(RECORD_SOURCE)
            except Exception:
                pass
            try:
                call_py.stop()
            except Exception:
                pass
            try:
                bot_app.stop()
            except Exception:
                pass
        except Exception:
            pass
