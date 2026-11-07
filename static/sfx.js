/* Verdict sounds for the projector.
 *
 * Synthesised, not sampled: no files to ship, no CDN, and the pitch is a
 * constant you can tune. A rising fifth for accepted, a low falling third for
 * rejected — short enough to sit under a human voice, quiet enough for a hall.
 *
 * Browsers start an AudioContext suspended until the page gets a gesture, so
 * nothing is audible until someone clicks or presses a key on the projector
 * laptop. That is expected: one click at setup, then it stays unlocked.
 */
(function () {
  'use strict';

  var MUTED_KEY = 'eb_sfx_muted';
  var PEAK = 0.15;

  function create() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    var ctx = null;
    var muted = window.localStorage.getItem(MUTED_KEY) === '1';

    function ensure() {
      if (!Ctx) return null;
      if (!ctx) {
        try {
          ctx = new Ctx();
        } catch (err) {
          return null;
        }
      }
      if (ctx.state === 'suspended') {
        ctx.resume().catch(function () {
          /* the browser will not resume without a gesture; the next click does */
        });
      }
      return ctx;
    }

    function tone(frequency, at, duration, peak, type) {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = type || 'sine';
      osc.frequency.setValueAtTime(frequency, at);
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(peak, at + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(at);
      osc.stop(at + duration + 0.03);
    }

    function play(notes) {
      if (muted) return;
      var audio = ensure();
      if (!audio || audio.state !== 'running') return;
      var now = audio.currentTime + 0.01;
      notes.forEach(function (note) {
        tone(note.f, now + note.at, note.d, note.g || PEAK, note.t);
      });
    }

    return {
      unlock: function () {
        ensure();
      },
      isMuted: function () {
        return muted;
      },
      toggle: function () {
        muted = !muted;
        window.localStorage.setItem(MUTED_KEY, muted ? '1' : '0');
        if (!muted) ensure();
        return muted;
      },
      accept: function () {
        play([
          { f: 587.33, at: 0, d: 0.13 }, // D5
          { f: 880.0, at: 0.1, d: 0.22, g: 0.17 } // A5
        ]);
      },
      reject: function () {
        play([
          { f: 233.08, at: 0, d: 0.16, t: 'triangle' }, // A#3
          { f: 174.61, at: 0.11, d: 0.28, g: 0.13, t: 'triangle' } // F3
        ]);
      }
    };
  }

  window.EngineerBotSfx = { create: create };
})();
