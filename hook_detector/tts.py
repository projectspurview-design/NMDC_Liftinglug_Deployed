from __future__ import annotations

import io
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

from piper import PiperVoice


@dataclass(frozen=True)
class _QueuedSequence:
    generation: int
    messages: tuple[str, ...]
    channel: object | None = None
    channel_generation: int | None = None


class TTSManager:
    def __init__(self) -> None:
        voice_path_value = os.environ.get("PIPER_VOICE_PATH")
        if voice_path_value:
            voice_path = Path(voice_path_value)
        elif os.name == "nt":
            app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            voice_path = app_data / "NMDC_HookDetector" / "voices" / "hi_IN-rohan-medium.onnx"
        else:
            data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
            voice_path = data_home / "nmdc-hook-detector" / "voices" / "hi_IN-rohan-medium.onnx"

        if not voice_path.is_file():
            raise FileNotFoundError(
                f"Piper voice model not found: {voice_path}. Download it with "
                f"python -m piper.download_voices --download-dir \"{voice_path.parent}\" hi_IN-rohan-medium."
            )

        self.voice = PiperVoice.load(voice_path)
        self._messages = queue.Queue(maxsize=16)
        self._sequence_lock = threading.Lock()
        self._sequence_generation = 0
        self._channel_generations: dict[object, int] = {}
        self._cancel_event = threading.Event()
        self._stop_event = threading.Event()
        self._player_lock = threading.Lock()
        self._player = None
        self._active_channel = None
        self._worker = threading.Thread(target=self._play_messages, daemon=True)
        self._worker.start()

    def speak(self, message: str) -> bool:
        if not message:
            return False

        with self._sequence_lock:
            try:
                self._messages.put_nowait(message)
            except queue.Full:
                return False
        return True

    def speak_sequence(self, *messages: str, channel: object | None = None) -> bool:
        sequence = tuple(message for message in messages if message)
        if not sequence:
            return False

        with self._sequence_lock:
            channel_generation = (
                None if channel is None else self._channel_generations.get(channel, 0)
            )
            queued_sequence = _QueuedSequence(
                self._sequence_generation,
                sequence,
                channel,
                channel_generation,
            )
            try:
                self._messages.put_nowait(queued_sequence)
            except queue.Full:
                return False
        return True

    def cancel_pending(self, channel: object | None = None) -> None:
        cancel_active = channel is None
        with self._sequence_lock:
            if channel is None:
                self._sequence_generation += 1
            else:
                self._channel_generations[channel] = self._channel_generations.get(channel, 0) + 1
            retained_messages = []
            while True:
                try:
                    queued_message = self._messages.get_nowait()
                except queue.Empty:
                    break
                else:
                    self._messages.task_done()
                    if channel is None or (
                        isinstance(queued_message, _QueuedSequence)
                        and queued_message.channel == channel
                    ):
                        continue
                    retained_messages.append(queued_message)
            for queued_message in retained_messages:
                self._messages.put_nowait(queued_message)

        with self._player_lock:
            if channel is not None and self._active_channel == channel:
                cancel_active = True
            if cancel_active:
                self._cancel_event.set()
            player = self._player
        if cancel_active and player is not None:
            try:
                player.terminate()
            except OSError:
                pass

        if cancel_active and os.name == "nt":
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)

    def _play_messages(self) -> None:
        while not self._stop_event.is_set():
            try:
                queued_messages = self._messages.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if isinstance(queued_messages, _QueuedSequence):
                    for message in queued_messages.messages:
                        with self._sequence_lock:
                            cancelled = queued_messages.generation != self._sequence_generation
                            if queued_messages.channel is not None:
                                cancelled = cancelled or queued_messages.channel_generation != (
                                    self._channel_generations.get(queued_messages.channel, 0)
                                )
                            if not cancelled:
                                self._cancel_event.clear()
                                with self._player_lock:
                                    self._active_channel = queued_messages.channel
                        if self._stop_event.is_set() or cancelled:
                            break
                        try:
                            self._speak_now(message)
                        except Exception as error:
                            print(f"Unable to play voice alert: {error}")
                        finally:
                            with self._player_lock:
                                self._active_channel = None
                elif not self._stop_event.is_set():
                    with self._sequence_lock:
                        self._cancel_event.clear()
                    try:
                        self._speak_now(queued_messages)
                    except Exception as error:
                        print(f"Unable to play voice alert: {error}")
            finally:
                self._messages.task_done()

    def stop(self) -> None:
        self._stop_event.set()
        while True:
            try:
                self._messages.get_nowait()
            except queue.Empty:
                break
            else:
                self._messages.task_done()

        with self._player_lock:
            player = self._player
        if player is not None:
            try:
                player.terminate()
                player.wait(timeout=1)
            except subprocess.TimeoutExpired:
                player.kill()
                player.wait()
            except OSError:
                pass

        if os.name == "nt":
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        self._worker.join(timeout=2)

    def _speak_now(self, message: str) -> None:
        audio_buffer = io.BytesIO()
        with wave.open(audio_buffer, "wb") as wav_file:
            self.voice.synthesize_wav(message, wav_file)

        if os.name == "nt":
            import winsound

            if self._stop_event.is_set() or self._cancel_event.is_set():
                return
            winsound.PlaySound(audio_buffer.getvalue(), winsound.SND_MEMORY)
            return

        players = {
            name: shutil.which(name)
            for name in ("paplay", "pw-play", "aplay")
        }
        player = next((players[name] for name in players if players[name]), None)
        if player is None:
            raise RuntimeError("Install pulseaudio-utils, pipewire-audio, or alsa-utils to play voice alerts.")

        audio_sink = os.environ.get("NMDC_HOOK_AUDIO_SINK")
        player_args = [player]
        if audio_sink:
            if player.endswith("/paplay"):
                player_args.extend(("--device", audio_sink))
            elif player.endswith("/pw-play"):
                player_args.extend(("--target", audio_sink))
            else:
                raise RuntimeError("NMDC_HOOK_AUDIO_SINK requires paplay or pw-play.")

        if self._stop_event.is_set() or self._cancel_event.is_set():
            return

        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            audio_file.write(audio_buffer.getvalue())
            audio_file.flush()
            process = subprocess.Popen([*player_args, audio_file.name])
            with self._player_lock:
                self._player = process
                should_stop = self._stop_event.is_set() or self._cancel_event.is_set()
            if should_stop:
                process.terminate()
            while True:
                try:
                    return_code = process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if self._stop_event.is_set() or self._cancel_event.is_set():
                        process.terminate()
            with self._player_lock:
                self._player = None
            if return_code and not self._stop_event.is_set() and not self._cancel_event.is_set():
                raise subprocess.CalledProcessError(return_code, [player, audio_file.name])
