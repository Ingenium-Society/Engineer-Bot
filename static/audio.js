/* Verdict sounds for the projector.
 *
 * Synthesised with the Web Audio API instead of shipping audio files: no
 * assets to copy to the venue laptop, nothing to cache, and it works offline.
 *
 * Browsers keep an AudioContext suspended until the page has seen a gesture,
 * so a kiosk that only ever displays needs Chrome started with
 * --autoplay-policy=no-user-gesture-required. Any click or key press also
 * unlocks it, which covers the operator using the same machine.
 */
(function () {
  'use strict';

  var ctx = null;

  function context() {
    if (ctx) return ctx;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    try {
      ctx = new Ctx();
    } catch (err) {
      return null;
    }
    return ctx;
  }

  function unlock() {
    var audio = context();
    if (audio && audio.state === 'suspended') {
      var resumed = audio.resume();
      if (resumed && resumed.catch) resumed.catch(function () {});
    }
  }

  function tone(freq, delay, duration, type, peak) {
    var audio = context();
    if (!audio) return;
    var at = audio.currentTime + delay;
    var osc = audio.createOscillator();
    var gain = audio.createGain();
    osc.type = type || 'sine';
    osc.frequency.setValueAtTime(freq, at);
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.linearRampToValueAtTime(peak, at + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + duration);
    osc.connect(gain);
    gain.connect(audio.destination);
    osc.start(at);
    osc.stop(at + duration + 0.03);
  }

  /* Rising fifth: bright, short, unmistakably "yes". */
  function accept() {
    unlock();
    tone(659.25, 0, 0.16, 'sine', 0.16); // E5
    tone(987.77, 0.1, 0.24, 'sine', 0.14); // B5
  }

  /* Falling minor third on a duller wave: "no", without being harsh. */
  function reject() {
    unlock();
    tone(220.0, 0, 0.18, 'triangle', 0.18); // A3
    tone(164.81, 0.13, 0.3, 'triangle', 0.16); // E3
  }

  window.EngineerBotSound = { accept: accept, reject: reject, unlock: unlock };

  // A kiosk tab may never be clicked; these catch the cases where it is.
  document.addEventListener('pointerdown', unlock);
  document.addEventListener('keydown', unlock);
})();
