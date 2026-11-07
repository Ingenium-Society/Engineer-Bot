/* Voice input for The Engineer-Bot.
 *
 * Two engines behind one interface:
 *   browser — Web Speech API (needs an internet-reachable speech service)
 *   server  — MediaRecorder + POST /api/stt/{team} (works offline, phase 2)
 *
 * Push-to-talk only. Nothing here opens the microphone on its own, and nothing
 * here ever submits anything: it hands a transcript back to app.js.
 */
(function () {
  'use strict';

  var DEFAULT_MAX_MS = 6000; // hard ceiling on one recording
  var DEFAULT_TAIL_MS = 700; // silence after speech before we stop
  var SPEECH_LEVEL = 6; // byte-domain peak; room noise sits below this

  /* MediaRecorder does not know when the speaker stopped. Without this the
   * recorder runs to its ceiling, which turns a 2 s command into an 8 s wait:
   * 6 s of recording, then the upload, then the countdown. */
  function watchSpeech(stream, options) {
    var AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return function () {};
    var ctx;
    try {
      ctx = new AudioCtx();
    } catch (err) {
      return function () {};
    }
    var source = ctx.createMediaStreamSource(stream);
    var analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    var buffer = new Uint8Array(analyser.fftSize);
    var heardAt = 0;
    var closed = false;

    function cleanup() {
      if (closed) return;
      closed = true;
      clearInterval(timer);
      try {
        source.disconnect();
      } catch (err) {
        /* ignore */
      }
      try {
        ctx.close();
      } catch (err) {
        /* ignore */
      }
    }

    var timer = setInterval(function () {
      analyser.getByteTimeDomainData(buffer);
      var peak = 0;
      for (var i = 0; i < buffer.length; i += 1) {
        var amplitude = Math.abs(buffer[i] - 128);
        if (amplitude > peak) peak = amplitude;
      }
      var now = window.performance.now();
      if (peak >= SPEECH_LEVEL) {
        heardAt = now;
        return;
      }
      if (heardAt && now - heardAt > options.tailMs) {
        cleanup();
        options.onDone();
      }
    }, 50);

    return cleanup;
  }

  function BrowserRecognition() {
    return window.SpeechRecognition || window.webkitSpeechRecognition || null;
  }

  function browserSupported() {
    return !!BrowserRecognition();
  }

  function mediaSupported() {
    return !!(window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  function mapError(code) {
    if (code === 'not-allowed' || code === 'service-not-allowed') return 'denied';
    if (code === 'audio-capture') return 'no-mic';
    if (code === 'network') return 'offline';
    if (code === 'no-speech' || code === 'aborted') return 'ok';
    return 'unsupported';
  }

  function createBrowserEngine(options) {
    var stallMs = options.maxMs || DEFAULT_MAX_MS;
    var rec = null;
    var listening = false;
    var silenceTimer = null;
    var finalText = '';
    var interimText = '';
    var startedAt = 0;
    var aborted = false;

    function clearTimer() {
      if (silenceTimer) {
        clearTimeout(silenceTimer);
        silenceTimer = null;
      }
    }

    function arm() {
      clearTimer();
      silenceTimer = setTimeout(stop, stallMs);
    }

    function stop() {
      if (rec && listening) {
        try {
          rec.stop();
        } catch (err) {
          /* already stopped */
        }
      }
    }

    function abort() {
      clearTimer();
      listening = false;
      aborted = true;
      if (rec) {
        try {
          rec.abort();
        } catch (err) {
          /* ignore */
        }
      }
      rec = null;
    }

    function deliver(text, ms) {
      clearTimer();
      listening = false;
      var trimmed = String(text || '').trim();
      if (!trimmed) {
        if (options.onEmpty) options.onEmpty();
        return;
      }
      if (options.onFinal) options.onFinal(trimmed, ms);
    }

    function start() {
      if (listening) return;
      var Recognition = BrowserRecognition();
      if (!Recognition) {
        if (options.onStatus) options.onStatus('unsupported');
        if (options.onError) options.onError('unsupported');
        return;
      }
      rec = new Recognition();
      rec.lang = options.lang || 'id-ID';
      rec.continuous = false;
      rec.interimResults = true;
      rec.maxAlternatives = 3;
      finalText = '';
      interimText = '';
      aborted = false;

      rec.onstart = function () {
        listening = true;
        startedAt = window.performance.now();
        if (options.onStatus) options.onStatus('ok');
        if (options.onStart) options.onStart();
        arm();
      };

      rec.onresult = function (event) {
        arm();
        var interim = '';
        for (var i = event.resultIndex; i < event.results.length; i += 1) {
          var result = event.results[i];
          var piece = result[0] ? result[0].transcript : '';
          if (result.isFinal) finalText += piece;
          else interim += piece;
        }
        interimText = interim;
        if (interim && options.onInterim) options.onInterim(interim);
        if (finalText) {
          var ms = Math.round(window.performance.now() - startedAt);
          stop();
          deliver(finalText, ms);
        }
      };

      rec.onerror = function (event) {
        clearTimer();
        listening = false;
        var status = mapError(event.error);
        if (options.onStatus) options.onStatus(status);
        if (status !== 'ok' && options.onError) options.onError(status);
      };

      rec.onend = function () {
        clearTimer();
        var wasListening = listening;
        listening = false;
        if (aborted) {
          aborted = false;
          return;
        }
        if (finalText) return;
        if (wasListening && interimText.trim()) {
          deliver(interimText, Math.round(window.performance.now() - startedAt));
        } else if (wasListening && options.onEmpty) {
          options.onEmpty();
        }
      };

      try {
        rec.start();
      } catch (err) {
        listening = false;
        if (options.onStatus) options.onStatus('unsupported');
        if (options.onError) options.onError('unsupported');
      }
    }

    return {
      engine: 'browser',
      start: start,
      stop: stop,
      abort: abort,
      isListening: function () {
        return listening;
      }
    };
  }

  /* MediaRecorder -> POST /api/stt/{team}. Used when the laptops have no internet. */
  function createServerEngine(options) {
    var team = options.team || 'a';
    var recorder = null;
    var chunks = [];
    var stream = null;
    var listening = false;
    var startedAt = 0;
    var stopWatch = null;
    var mimeType = '';
    // abort() stops the recorder and releases it immediately, but onstop still
    // fires afterwards: by then `recorder` is null and the clip is unwanted.
    var discarded = false;

    function release() {
      if (stopWatch) {
        stopWatch();
        stopWatch = null;
      }
      if (stream) {
        stream.getTracks().forEach(function (track) {
          track.stop();
        });
        stream = null;
      }
      recorder = null;
    }

    function abort() {
      discarded = true;
      listening = false;
      if (recorder && recorder.state !== 'inactive') {
        try {
          recorder.stop();
        } catch (err) {
          /* ignore */
        }
      }
      chunks = [];
      release();
    }

    function stop() {
      if (recorder && recorder.state !== 'inactive') {
        try {
          recorder.stop();
        } catch (err) {
          /* ignore */
        }
      }
    }

    function start() {
      if (listening) return;
      if (!mediaSupported()) {
        if (options.onStatus) options.onStatus('unsupported');
        if (options.onError) options.onError('unsupported');
        return;
      }
      navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then(function (mediaStream) {
          stream = mediaStream;
          chunks = [];
          discarded = false;
          mimeType = pickMime();
          recorder = new MediaRecorder(mediaStream, { mimeType: mimeType });
          recorder.ondataavailable = function (event) {
            if (event.data && event.data.size) chunks.push(event.data);
          };
          recorder.onstop = function () {
            var elapsed = Math.round(window.performance.now() - startedAt);
            var blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
            var skip = discarded;
            chunks = [];
            discarded = false;
            release();
            listening = false;
            if (!skip) upload(blob, elapsed);
          };
          recorder.start();
          listening = true;
          startedAt = window.performance.now();
          if (options.onStatus) options.onStatus('ok');
          if (options.onStart) options.onStart();
          // Stop when the player stops talking; the ceiling is only a backstop
          // for a room too loud for the level test.
          stopWatch = watchSpeech(mediaStream, {
            tailMs: options.tailMs || DEFAULT_TAIL_MS,
            onDone: stop
          });
          setTimeout(function () {
            if (listening) stop();
          }, options.maxMs || DEFAULT_MAX_MS);
        })
        .catch(function (err) {
          var status = err && err.name === 'NotAllowedError' ? 'denied' : 'no-mic';
          if (options.onStatus) options.onStatus(status);
          if (options.onError) options.onError(status);
        });
    }

    function pickMime() {
      var candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
      for (var i = 0; i < candidates.length; i += 1) {
        if (window.MediaRecorder.isTypeSupported(candidates[i])) return candidates[i];
      }
      return '';
    }

    function upload(blob, elapsed) {
      var form = new FormData();
      form.append('audio', blob, 'clip.webm');
      fetch('/api/stt/' + team, { method: 'POST', body: form })
        .then(function (response) {
          if (!response.ok) throw new Error('http ' + response.status);
          return response.json();
        })
        .then(function (data) {
          var text = String((data && data.text) || '').trim();
          var ms = (data && data.ms) || elapsed;
          if (!text) {
            if (options.onEmpty) options.onEmpty();
            return;
          }
          if (options.onFinal) options.onFinal(text, ms);
        })
        .catch(function () {
          if (options.onStatus) options.onStatus('offline');
          if (options.onError) options.onError('offline');
        });
    }

    return {
      engine: 'server',
      start: start,
      stop: stop,
      abort: abort,
      isListening: function () {
        return listening;
      }
    };
  }

  window.EngineerBotVoice = {
    supported: browserSupported,
    serverSupported: mediaSupported,
    create: function (mode, options) {
      if (mode === 'server' && mediaSupported()) return createServerEngine(options);
      if (browserSupported()) return createBrowserEngine(options);
      return null;
    }
  };
})();
