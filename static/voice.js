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

  var DEFAULT_SILENCE_MS = 6000;

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
    var silenceMs = options.silenceMs || DEFAULT_SILENCE_MS;
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
      silenceTimer = setTimeout(stop, silenceMs);
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

    function release() {
      if (stream) {
        stream.getTracks().forEach(function (track) {
          track.stop();
        });
        stream = null;
      }
      recorder = null;
    }

    function abort() {
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
          recorder = new MediaRecorder(mediaStream, { mimeType: pickMime() });
          recorder.ondataavailable = function (event) {
            if (event.data && event.data.size) chunks.push(event.data);
          };
          recorder.onstop = function () {
            var elapsed = Math.round(window.performance.now() - startedAt);
            var blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
            chunks = [];
            release();
            listening = false;
            upload(blob, elapsed);
          };
          recorder.start();
          listening = true;
          startedAt = window.performance.now();
          if (options.onStatus) options.onStatus('ok');
          if (options.onStart) options.onStart();
          // Push-to-talk: the button stops it, with a hard ceiling as a backstop.
          setTimeout(function () {
            if (listening) stop();
          }, options.silenceMs || DEFAULT_SILENCE_MS);
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
