/* The Engineer-Bot — plain browser JavaScript, no build step, no CDN. */
(function () {
  'use strict';

  var TEAM_IDS = ['a', 'b'];
  var MILESTONES = 7;

  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function fmtClock(seconds) {
    var total = Math.max(0, Math.round(seconds || 0));
    var minutes = Math.floor(total / 60);
    var rest = total % 60;
    return (minutes < 10 ? '0' : '') + minutes + ':' + (rest < 10 ? '0' : '') + rest;
  }

  function fmtTurn(seconds) {
    return String(Math.max(0, Math.ceil(seconds || 0)));
  }

  function socketPath(path) {
    var scheme = location.protocol === 'https:' ? 'wss://' : 'ws://';
    return scheme + location.host + path;
  }

  /* Reconnect forever: a laptop lid closing mid-round must not kill the page. */
  function connect(path, onMessage, onState) {
    var socket = null;
    var queue = [];

    function open() {
      socket = new WebSocket(socketPath(path));
      socket.onopen = function () {
        if (onState) onState(true);
        while (queue.length) {
          socket.send(queue.shift());
        }
      };
      socket.onmessage = function (event) {
        var payload;
        try {
          payload = JSON.parse(event.data);
        } catch (err) {
          return;
        }
        onMessage(payload);
      };
      socket.onclose = function () {
        if (onState) onState(false);
        setTimeout(open, 1000);
      };
      socket.onerror = function () {
        if (socket) socket.close();
      };
    }

    open();

    return {
      send: function (payload) {
        var text = JSON.stringify(payload);
        if (socket && socket.readyState === WebSocket.OPEN) {
          socket.send(text);
        } else {
          queue.push(text);
        }
      }
    };
  }

  /* ------------------------------------------------------------------ team */

  function initTeam() {
    var match = location.pathname.match(/\/team\/([ab])/);
    var teamId = match ? match[1] : 'a';

    var els = {
      name: $('#team-name'),
      counter: $('#counter'),
      clock: $('#turn-clock'),
      status: $('#status'),
      form: $('#answer-form'),
      input: $('#answer'),
      send: $('#send'),
      mic: $('#mic'),
      voice: $('#voice'),
      voiceLine: $('#voice-line'),
      hint: $('#hint'),
      history: $('#history'),
      round: $('#round-clock'),
      banner: $('#banner'),
      bannerTitle: $('#banner-title'),
      bannerText: $('#banner-text'),
      tts: $('#tts')
    };

    els.name.textContent = teamId === 'a' ? 'TIM A' : 'TIM B';
    document.title = els.name.textContent + ' · The Engineer-Bot';

    var state = null;
    var connected = true;
    var spoken = 0;
    var ttsEnabled = localStorage.getItem('eb_tts') === '1';
    els.tts.checked = ttsEnabled;

    els.tts.addEventListener('change', function () {
      ttsEnabled = els.tts.checked;
      localStorage.setItem('eb_tts', ttsEnabled ? '1' : '0');
      if (!ttsEnabled && window.speechSynthesis) window.speechSynthesis.cancel();
    });

    function speak(text) {
      if (!ttsEnabled || !window.speechSynthesis) return;
      var utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = 'id-ID';
      utterance.rate = 0.95;
      window.speechSynthesis.speak(utterance);
    }

    /* ---- voice input: push-to-talk only, never always-on ---- */

    var voice = {
      mode: 'off',
      ready: false,
      engine: null,
      usable: false,
      status: 'ok',
      listening: false,
      raw: '',
      sttMs: null,
      timer: null,
      delayMs: 2000
    };

    function canSubmit() {
      return !!state && state.phase === 'TYPING' && !state.round_over && !state.paused;
    }

    function reportStt(status) {
      voice.status = status;
      voice.usable = !!voice.engine && status === 'ok';
      if (link) {
        link.send({
          type: 'stt_status',
          status: status,
          engine: voice.engine ? voice.engine.engine : null
        });
      }
    }

    function voiceLine(text, options) {
      options = options || {};
      els.voice.hidden = false;
      els.voiceLine.className = 'voice-line' + (options.tone ? ' ' + options.tone : '');
      els.voiceLine.textContent = '';
      els.voiceLine.appendChild(document.createTextNode(text));
      if (options.label && options.action) {
        var again = document.createElement('button');
        again.type = 'button';
        again.className = 'link';
        again.textContent = options.label;
        again.addEventListener('click', options.action);
        els.voiceLine.appendChild(document.createTextNode(' · '));
        els.voiceLine.appendChild(again);
      }
    }

    function hideVoice() {
      els.voice.hidden = true;
      els.voiceLine.textContent = '';
    }

    // The transcript is typed into the box, never sent on its own: the player
    // reads it, fixes what the engine misheard, then presses Kirim.
    function showTranscript() {
      els.voice.hidden = false;
      els.voiceLine.className = 'voice-line live';
      els.voiceLine.textContent = '';
      els.voiceLine.appendChild(
        document.createTextNode('Kamu bilang: "' + voice.raw + '" · ')
      );
      var again = document.createElement('button');
      again.type = 'button';
      again.className = 'link';
      again.textContent = 'Ulangi';
      again.addEventListener('click', startListening);
      els.voiceLine.appendChild(again);
      els.voiceLine.appendChild(
        document.createTextNode(' · periksa lalu tekan Kirim')
      );
    }

    function startListening() {
      if (!voice.usable || !voice.engine || voice.listening || !canSubmit()) return;
      hideVoice();
      // Never listen while the robot is speaking.
      if (window.speechSynthesis && window.speechSynthesis.speaking) {
        window.speechSynthesis.cancel();
      }
      els.mic.classList.add('listening');
      voiceLine('Mendengarkan...', { tone: 'live' });
      voice.engine.start();
    }

    function stopListening() {
      voice.listening = false;
      els.mic.classList.remove('listening');
      if (voice.engine) voice.engine.abort();
    }

    function toggleListening() {
      if (voice.listening) {
        stopListening();
        hideVoice();
        return;
      }
      startListening();
    }

    function setupVoice(view) {
      var mode = view.stt_mode || 'off';
      if (mode === voice.mode && voice.ready) return;
      voice.mode = mode;
      voice.ready = true;

      if (mode === 'off') {
        els.mic.hidden = true;
        voice.usable = false;
        hideVoice();
        return;
      }
      els.mic.hidden = false;

      if (!voice.engine && window.EngineerBotVoice) {
        voice.engine = window.EngineerBotVoice.create(mode, {
          lang: view.stt_lang || 'id-ID',
          team: teamId,
          tailMs: 700,
          maxMs: 6000,
          onStart: function () {
            voice.listening = true;
            els.mic.classList.add('listening');
          },
          onInterim: function (text) {
            voiceLine('Mendengarkan... ' + text, { tone: 'live' });
          },
          onFinal: function (text, ms) {
            voice.listening = false;
            els.mic.classList.remove('listening');
            if (!canSubmit()) {
              hideVoice();
              return;
            }
            voice.raw = text;
            voice.sttMs = ms;
            els.input.value = text;
            pushDraft(text);
            els.input.focus();
            showTranscript();
          },
          onEmpty: function () {
            voice.listening = false;
            els.mic.classList.remove('listening');
            voiceLine('Tidak terdengar. Coba lagi.', {
              tone: 'error',
              label: 'Ulangi',
              action: startListening
            });
          },
          onStatus: function (status) {
            reportStt(status);
          },
          onError: function () {
            voice.listening = false;
            els.mic.classList.remove('listening');
            voiceLine('Mikrofon tidak tersedia, silakan ketik', { tone: 'error' });
          }
        });
      }

      if (!voice.engine) {
        voice.usable = false;
        reportStt('unsupported');
        voiceLine('Mikrofon tidak tersedia, silakan ketik', { tone: 'error' });
        return;
      }

      // The engine exists, so the button works until the browser says otherwise.
      // onStatus() downgrades this the moment permission or the network fails.
      voice.usable = true;
    }

    function teamLabel(teamId) {
      return teamId === 'a' ? 'Tim A' : 'Tim B';
    }

    function statusFor(view) {
      if (!connected) return 'Menyambung ulang…';
      if (view.round_over) {
        return view.winner === teamId ? 'Tim kami menang!' : 'Ronde selesai';
      }
      if (view.paused) return 'Ronde dijeda';
      if (view.notice) return view.notice;
      if (view.phase === 'IDLE') return 'Menunggu ronde dimulai';
      if (view.phase === 'WAITING') {
        return view.turn ? 'Menunggu giliran ' + teamLabel(view.turn) : 'Menunggu giliran';
      }
      if (view.phase === 'TYPING') {
        if (view.last && view.last.status === 'REJECT') return 'Ditolak ✘ ' + view.last.message;
        if (view.used >= view.max_players - 1) return 'Instruksi terakhir!';
        return voice.usable
          ? 'Giliranmu! Bicara atau ketik satu instruksi'
          : 'Giliranmu! Ketik satu instruksi';
      }
      if (view.phase === 'JUDGING') return 'Menunggu juri...';
      if (view.phase === 'LOCKED') {
        if (view.last && view.last.status === 'ACCEPT') {
          return 'Diterima ✔ Menunggu robot selesai';
        }
        return 'Menunggu operator';
      }
      return 'Instruksi habis';
    }

    function renderHistory(view) {
      var items = view.history || [];
      els.history.innerHTML = '';
      items.forEach(function (entry) {
        var li = document.createElement('li');
        li.className = entry.status === 'ACCEPT' ? 'accept' : 'reject';
        var number = document.createElement('span');
        number.className = 'num';
        number.textContent = '#' + (entry.player || '');
        var body = document.createElement('div');
        var text = document.createElement('div');
        text.className = 'text';
        text.textContent = entry.text || '(kosong)';
        var message = document.createElement('div');
        message.className = 'msg';
        message.textContent = entry.status + ' · ' + entry.message;
        body.appendChild(text);
        body.appendChild(message);
        li.appendChild(number);
        li.appendChild(body);
        els.history.appendChild(li);
      });
      els.history.scrollTop = els.history.scrollHeight;

      var accepted = items.filter(function (entry) {
        return entry.status === 'ACCEPT';
      });
      if (accepted.length > spoken) {
        speak(accepted[accepted.length - 1].text);
        spoken = accepted.length;
      }
    }

    function render(view) {
      var wasTyping = state && state.phase === 'TYPING';
      state = view;
      setupVoice(view);

      els.counter.textContent =
        'Pemain ' + view.player + ' dari ' + view.max_players +
        ' · Sisa instruksi: ' + view.remaining;
      var timing = view.phase === 'TYPING';
      els.clock.textContent = timing ? fmtTurn(view.turn_seconds_left) : '—';
      els.clock.className = 'turn-clock' +
        (timing ? (view.turn_seconds_left <= 5 ? ' urgent' : '') : ' idle');
      els.round.textContent = 'Sisa ronde ' + fmtClock(view.round_seconds_left);

      var text = statusFor(view);
      els.status.textContent = text;
      els.status.className = 'status-line' +
        (text.indexOf('Diterima') === 0 ? ' accept' : '') +
        (text.indexOf('Ditolak') === 0 ? ' reject' : '');

      var editable = view.phase === 'TYPING' && !view.round_over && !view.paused;
      els.input.disabled = !editable;
      els.send.disabled = !editable;
      els.mic.hidden = voice.mode === 'off' || !voice.engine;
      els.mic.disabled = !editable || !voice.usable;
      // The turn ended (or was paused): the microphone must close immediately.
      if (!editable && voice.listening) {
        stopListening();
        hideVoice();
      }
      if (editable && !wasTyping) {
        els.input.focus();
      }
      if (!editable) els.input.value = '';

      renderHistory(view);

      if (view.round_over) {
        els.banner.hidden = false;
        els.bannerTitle.textContent = view.winner === teamId ? 'Tim kami menang!' : 'Ronde selesai';
        els.bannerText.textContent = view.winner
          ? (view.winner === teamId ? 'Toples bersih sudah di area finish.' : 'Tim lain lebih dulu selesai.')
          : 'Tidak ada pemenang mutlak.';
      } else {
        els.banner.hidden = true;
      }
    }

    els.form.addEventListener('submit', function (event) {
      event.preventDefault();
      hideVoice();
      if (voice.listening) stopListening();
      var text = els.input.value.trim();
      if (!text || !state || state.phase !== 'TYPING') return;
      var payload = { type: 'submit', text: text };
      if (voice.raw) {
        // Keep what the microphone heard, even if the player edited it after.
        payload.raw = voice.raw;
        if (voice.sttMs !== null && voice.sttMs !== undefined) payload.stt_ms = voice.sttMs;
        if (text === voice.raw.trim()) payload.source = 'voice';
      }
      link.send(payload);
      pushDraft('');
      els.input.value = '';
      els.input.disabled = true;
      els.send.disabled = true;
      els.status.textContent = 'Memproses...';
      els.status.className = 'status-line';
    });

    els.mic.addEventListener('click', toggleListening);

    // The projector mirrors what the team is typing, so the room can read along.
    var draftTimer = null;
    var draftPending = '';

    function pushDraft(text) {
      draftPending = text || '';
      if (draftTimer) return;
      draftTimer = setTimeout(function () {
        draftTimer = null;
        link.send({ type: 'draft', text: draftPending });
      }, 120);
    }

    els.input.addEventListener('input', function () {
      pushDraft(els.input.value);
    });

    // Space starts listening whenever the text box does not have focus.
    document.addEventListener('keydown', function (event) {
      if (event.code !== 'Space' && event.key !== ' ') return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      var active = document.activeElement;
      if (active === els.input) return;
      var tag = active && active.tagName ? active.tagName.toLowerCase() : '';
      if (tag === 'input' || tag === 'textarea') return;
      if (!canSubmit() || !voice.usable) return;
      event.preventDefault();
      startListening();
    });

    var link = connect('/ws/team/' + teamId, render, function (up) {
      connected = up;
      if (state) render(state);
    });
  }

  /* ----------------------------------------------------------------- admin */

  function initAdmin() {
    var state = null;
    var connected = true;
    var keyA = localStorage.getItem('eb_key_a') || 'a';
    var keyB = localStorage.getItem('eb_key_b') || 'l';

    var els = {
      clock: $('#round-clock'),
      columns: $('#columns'),
      start: $('#btn-start'),
      pause: $('#btn-pause'),
      resume: $('#btn-resume'),
      reset: $('#btn-reset'),
      ai: $('#f-ai'),
      model: $('#f-model'),
      latency: $('#f-latency'),
      turn: $('#f-turn'),
      chip: $('#turn-chip'),
      keyA: $('#key-a'),
      keyB: $('#key-b')
    };

    els.keyA.value = keyA;
    els.keyB.value = keyB;

    function column(teamId) {
      var label = teamId === 'a' ? 'TIM A' : 'TIM B';
      var section = document.createElement('section');
      section.className = 'card';
      section.dataset.team = teamId;
      section.innerHTML =
        '<div class="card-head">' +
          '<h2>' + label + '</h2>' +
          '<span class="pill" data-role="phase">idle</span>' +
        '</div>' +
        '<div class="progress"><span data-role="bar"></span></div>' +
        '<div class="stat-row">' +
          '<span>Pemain <b data-role="player">-</b></span>' +
          '<span>Instruksi <b data-role="used">-</b></span>' +
          '<span>Milestone <b data-role="milestones">-</b>/' + MILESTONES + '</span>' +
        '</div>' +
        '<div class="stat-row stt-row">' +
          '<span>Suara <b data-role="stt">-</b></span>' +
          '<span>Mesin <b data-role="engine">-</b></span>' +
          '<span>Teks <b data-role="raw">-</b></span>' +
        '</div>' +
        '<div class="last-box">' +
          '<div class="cmd" data-role="last-text">Belum ada instruksi.</div>' +
          '<div class="meta" data-role="last-meta"></div>' +
        '</div>' +
        '<div class="pending" data-role="pending" hidden>' +
          '<div class="pending-head">Menunggu konfirmasi</div>' +
          '<div class="pending-text" data-role="pending-text"></div>' +
          '<div class="pending-verdict" data-role="pending-verdict"></div>' +
          '<div class="pending-cmd" data-role="pending-cmd"></div>' +
          '<div class="pending-reason" data-role="pending-reason"></div>' +
        '</div>' +
        '<div class="force-row">' +
          '<button class="accept" data-action="force-accept">Terima</button>' +
          '<button class="reject" data-action="force-reject">Tolak</button>' +
        '</div>' +
        '<div class="big-buttons">' +
          '<button class="primary" data-action="done">Selesai</button>' +
          '<button data-action="redo">Ulang</button>' +
        '</div>' +
        '<div class="operator-row">' +
          '<input class="operator-input" data-role="operator" maxlength="80" ' +
          'placeholder="Ketik untuk pemain" autocomplete="off" spellcheck="false">' +
          '<button data-action="operator-send">Kirim</button>' +
        '</div>';
      return section;
    }

    TEAM_IDS.forEach(function (teamId) {
      els.columns.appendChild(column(teamId));
    });

    function setText(root, role, value) {
      var node = root.querySelector('[data-role="' + role + '"]');
      if (node) node.textContent = value;
    }

    function renderTeam(teamId, team) {
      var root = els.columns.querySelector('[data-team="' + teamId + '"]');
      if (!root) return;
      var pill = root.querySelector('[data-role="phase"]');
      var phase = team.phase;
      pill.textContent = phase.toLowerCase();
      pill.className = 'pill';
      if (phase === 'TYPING') pill.className = 'pill typing';
      if (phase === 'LOCKED') pill.className = 'pill locked';
      if (phase === 'DONE') pill.className = 'pill done';
      if (team.locked_seconds > 30) {
        pill.className = 'pill alert';
        pill.textContent = 'locked ' + Math.round(team.locked_seconds) + 's';
      }

      setText(root, 'player', String(team.player));
      setText(root, 'used', String(team.used));
      setText(root, 'milestones', String(team.milestones));
      root.querySelector('[data-role="bar"]').style.width =
        Math.round((team.milestones / MILESTONES) * 100) + '%';

      // Only offer the buttons that can actually do something right now.
      var locked = team.phase === 'LOCKED' && !team.round_over;
      var pending = team.pending_text != null;
      // The sentence arrives before the model's reading does.
      var ready = team.pending_status != null;
      root.querySelector('[data-action="done"]').disabled = !locked;
      root.querySelector('[data-action="redo"]').disabled = !locked;
      // Both buttons work as soon as there is a sentence: a dead model must
      // not stop the operator from moving the round on.
      root.querySelector('[data-action="force-accept"]').disabled = !pending;
      root.querySelector('[data-action="force-reject"]').disabled = !pending;
      // Two dead buttons on an idle card are just clutter.
      root.querySelector('.force-row').hidden = !pending;

      // Show what the judge proposes, not just that something is waiting.
      root.classList.toggle('awaiting', pending);
      var box = root.querySelector('[data-role="pending"]');
      box.hidden = !pending;
      if (pending) {
        setText(
          root,
          'pending-text',
          team.pending_text ? '"' + team.pending_text + '"' : ''
        );
        var verdict = root.querySelector('[data-role="pending-verdict"]');
        if (!ready) {
          verdict.className = 'pending-verdict notice';
          verdict.textContent = team.pending_error || 'AI sedang membaca…';
        } else {
          var accept = team.pending_status === 'ACCEPT';
          verdict.className = 'pending-verdict ' + (accept ? 'accept' : 'reject');
          verdict.textContent =
            (accept ? 'TERIMA' : 'TOLAK') + ' · ' + (team.pending_message || '');
        }
        setText(
          root,
          'pending-cmd',
          team.pending_command ? 'AI baca: ' + team.pending_command : ''
        );
        setText(
          root,
          'pending-reason',
          team.pending_reason ? '"' + team.pending_reason + '"' : ''
        );
      }

      var stt = team.stt_status || '-';
      if (team.stt_ms != null) stt += ' · ' + Math.round(team.stt_ms) + ' ms';
      setText(root, 'stt', stt);
      setText(root, 'engine', team.stt_engine || '-');
      setText(root, 'raw', team.last_raw ? '"' + team.last_raw + '"' : '-');

      var operatorInput = root.querySelector('[data-role="operator"]');
      var canType = team.phase === 'TYPING' && !team.round_over;
      operatorInput.disabled = !canType;
      root.querySelector('[data-action="operator-send"]').disabled = !canType;

      var last = team.last;
      // An empty "last instruction" box next to a pending sentence is noise.
      root.querySelector('.last-box').hidden = !last && pending;
      setText(
        root,
        'last-text',
        last ? last.text || '(kosong)' : (pending ? '' : 'Belum ada instruksi.')
      );
      var meta = [];
      if (last) meta.push(last.status + ' · ' + last.message);
      if (team.source) meta.push('sumber: ' + team.source);
      if (team.latency_ms != null) meta.push(Math.round(team.latency_ms) + ' ms');
      if (team.command) meta.push(team.command.verb);
      setText(root, 'last-meta', meta.join('  ·  '));
    }

    function render(view) {
      state = view;
      els.clock.textContent = fmtClock(view.round_seconds_left);
      els.ai.textContent = view.ai_available
        ? 'aktif' + (view.ai_error ? ' · ' + view.ai_error : '')
        : 'mati (belum diatur)';
      els.model.textContent = view.ai_model || '-';

      TEAM_IDS.forEach(function (teamId) {
        renderTeam(teamId, view.teams[teamId]);
      });

      var latency = view.teams.a.ai_latency_ms || view.teams.b.ai_latency_ms;
      var fallback = view.teams.a.fallback_used || view.teams.b.fallback_used;
      els.latency.innerHTML = latency != null
        ? (fallback ? '<span class="dot bad"></span>' : '<span class="dot ok"></span>') +
          Math.round(latency) + ' ms' + (fallback ? ' (fallback)' : '')
        : '<span class="dot"></span>-';

      var turnLabel = view.turn === 'a' ? 'Tim A' : (view.turn === 'b' ? 'Tim B' : '-');
      els.turn.textContent = turnLabel;
      if (!connected) {
        els.chip.textContent = 'TERPUTUS';
        els.chip.className = 'turn-chip';
      } else {
        els.chip.textContent = view.round_over
          ? 'SELESAI'
          : (view.turn ? 'GILIRAN ' + turnLabel.toUpperCase() : 'SIAP');
        els.chip.className = 'turn-chip' + (view.turn && !view.round_over ? ' active' : '');
      }

      els.start.disabled = view.started && !view.round_over;
      els.pause.disabled = !view.started || view.paused || view.round_over;
      els.resume.disabled = !view.paused;
    }

    els.columns.addEventListener('click', function (event) {
      var button = event.target.closest('button[data-action]');
      if (!button) return;
      var team = button.closest('[data-team]').dataset.team;
      var action = button.dataset.action;
      if (action === 'done') link.send({ type: 'done', team: team });
      if (action === 'redo') link.send({ type: 'redo', team: team });
      if (action === 'force-accept') link.send({ type: 'force', team: team, decision: 'accept' });
      if (action === 'force-reject') link.send({ type: 'force', team: team, decision: 'reject' });
      if (action === 'operator-send') {
        var input = button.closest('[data-team]').querySelector('[data-role="operator"]');
        var text = input.value.trim();
        if (!text) return;
        link.send({ type: 'operator_submit', team: team, text: text });
        input.value = '';
      }
    });

    els.columns.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') return;
      var input = event.target.closest ? event.target.closest('[data-role="operator"]') : null;
      if (!input) return;
      event.preventDefault();
      var team = input.closest('[data-team]').dataset.team;
      var text = input.value.trim();
      if (!text) return;
      link.send({ type: 'operator_submit', team: team, text: text });
      input.value = '';
    });

    els.start.addEventListener('click', function () { link.send({ type: 'start' }); });
    els.pause.addEventListener('click', function () { link.send({ type: 'pause' }); });
    els.resume.addEventListener('click', function () { link.send({ type: 'resume' }); });
    els.reset.addEventListener('click', function () {
      if (window.confirm('Reset ronde? Semua instruksi akan hilang.')) link.send({ type: 'reset' });
    });

    els.keyA.addEventListener('input', function () {
      keyA = els.keyA.value.toLowerCase();
      localStorage.setItem('eb_key_a', keyA);
    });
    els.keyB.addEventListener('input', function () {
      keyB = els.keyB.value.toLowerCase();
      localStorage.setItem('eb_key_b', keyB);
    });

    document.addEventListener('keydown', function (event) {
      var tag = (event.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || event.metaKey || event.ctrlKey) return;
      var key = (event.key || '').toLowerCase();
      var team = key === keyA ? 'a' : (key === keyB ? 'b' : null);
      if (!team) return;
      event.preventDefault();
      link.send({ type: event.shiftKey ? 'redo' : 'done', team: team });
    });

    var link = connect('/ws/admin', render, function (up) {
      connected = up;
      if (state) render(state);
    });
  }

  /* ---------------------------------------------------------------- screen */

  /* The projector runs on the server laptop. It is the room's only view of the
   * game, so it shows the instruction as it is typed, holds a processing beat,
   * then plays one verdict. `seq` is what makes "one verdict" reliable: a
   * replayed state never animates twice. */
  function initScreen() {
    var MIN_PROCESSING_MS = 700;
    var VERDICT_HOLD_MS = 1900;

    var els = {
      clock: $('#round-clock'),
      chip: $('#turn-chip'),
      stage: $('#stage'),
      turn: $('#stage-turn'),
      stageClock: $('#stage-clock'),
      text: $('#stage-text'),
      foot: $('#stage-foot'),
      overlay: $('#overlay'),
      overlayTitle: $('#overlay-title'),
      overlaySub: $('#overlay-sub')
    };

    var view = null;
    var shownSeq = -1;
    var processingAt = 0;
    var verdictUntil = 0;
    var holdTimer = null;
    var milestones = { a: -1, b: -1 };

    function label(teamId) {
      return teamId === 'b' ? 'TIM B' : 'TIM A';
    }

    function setStage(modifier, turnText, clockText, bodyText, footText, footTone) {
      els.stage.className = 'stage' + (modifier ? ' stage--' + modifier : '');
      els.turn.textContent = turnText;
      els.stageClock.className = 'stage-clock';
      els.stageClock.textContent = clockText;
      els.text.textContent = bodyText;
      els.text.className = 'stage-text' + (bodyText ? '' : ' empty');
      els.foot.textContent = footText || '';
      els.foot.className = 'stage-foot' + (footTone ? ' ' + footTone : '');
    }

    function paintTeams() {
      TEAM_IDS.forEach(function (teamId) {
        var team = view.teams[teamId];
        var panel = document.querySelector('.panel[data-team="' + teamId + '"]');
        if (!panel) return;

        panel.classList.toggle('on-turn', view.turn === teamId && !view.round_over);
        panel.classList.toggle('finished', team.phase === 'DONE');
        panel.querySelector('[data-role="left"]').textContent = String(team.remaining);

        var pips = panel.querySelector('[data-role="pips"]');
        if (!pips.children.length) {
          for (var i = 0; i < MILESTONES; i += 1) {
            var pip = document.createElement('span');
            pip.className = 'pip';
            pips.appendChild(pip);
          }
        }
        Array.prototype.forEach.call(pips.children, function (pip, index) {
          pip.className = 'pip' + (index < team.milestones ? ' on' : '');
        });
        // A milestone that just landed gets one pop, not a permanent animation.
        if (milestones[teamId] >= 0 && team.milestones > milestones[teamId]) {
          var fresh = pips.children[team.milestones - 1];
          if (fresh) {
            fresh.classList.add('pop');
            setTimeout(function () {
              fresh.classList.remove('pop');
            }, 500);
          }
        }
        milestones[teamId] = team.milestones;

        var last = panel.querySelector('[data-role="last"]');
        last.textContent = team.last ? team.last.message : '';
        last.className = 'panel-last ' + (team.last ? team.last.status.toLowerCase() : '');
      });
    }

    function paintProcessing() {
      setStage(
        'processing',
        view.turn ? 'GILIRAN ' + label(view.turn) : 'MEMPROSES',
        '',
        'MENUNGGU JURI…',
        view.pending_text ? '"' + view.pending_text + '"' : 'Juri memeriksa instruksi',
        ''
      );
    }

    function paintTyping() {
      var draft = (view.draft || '').trim();
      var paused = view.paused;
      setStage(
        paused ? 'idle' : 'typing',
        paused ? 'RONDE DIJEDA' : 'GILIRAN ' + label(view.turn),
        view.turn_seconds_left > 0 ? String(Math.ceil(view.turn_seconds_left)) : '',
        paused ? 'Ronde dijeda' : (draft || 'Menunggu instruksi…'),
        paused ? 'Tekan Lanjut di halaman operator' : '',
        ''
      );
      if (!paused) {
        els.stageClock.className = 'stage-clock' + (view.turn_seconds_left <= 5 ? ' urgent' : '');
        els.text.className = 'stage-text' + (draft ? ' caret' : ' empty');
      }
    }

    function showVerdict(last) {
      shownSeq = last.seq;
      processingAt = 0;
      verdictUntil = Date.now() + VERDICT_HOLD_MS;

      var accepted = last.status === 'ACCEPT';
      setStage(
        accepted ? 'accept' : 'reject',
        label(last.team) + ' · ' + (accepted ? 'DITERIMA' : 'DITOLAK'),
        '',
        last.text || '(kosong)',
        last.message,
        accepted ? 'accept' : 'reject'
      );

      if (holdTimer) clearTimeout(holdTimer);
      holdTimer = setTimeout(function () {
        holdTimer = null;
        if (view && !view.round_over && Date.now() >= verdictUntil) paint();
      }, VERDICT_HOLD_MS + 40);
    }

    function paintOverlay() {
      els.overlay.hidden = false;
      els.overlayTitle.textContent = view.winner ? label(view.winner) : 'SERI';
      if (view.winner) {
        var team = view.teams[view.winner];
        els.overlaySub.textContent =
          team.used + ' instruksi · ' + team.milestones + '/' + MILESTONES + ' milestone';
      } else {
        els.overlaySub.textContent = 'Tidak ada pemenang mutlak.';
      }
    }

    function paint() {
      if (!view) return;

      els.clock.textContent = fmtClock(view.round_seconds_left);
      els.clock.className =
        'board-clock' +
        (view.round_seconds_left <= 30 && !view.round_over ? ' urgent' : '');

      paintTeams();

      if (view.round_over) {
        els.chip.textContent = 'SELESAI';
        els.chip.className = 'turn-chip';
        paintOverlay();
        return;
      }
      els.overlay.hidden = true;

      els.chip.textContent = view.turn ? 'GILIRAN ' + label(view.turn) : 'SIAP';
      els.chip.className = 'turn-chip' + (view.turn ? ' active' : '');

      var now = Date.now();
      if (now < verdictUntil) return; // the verdict is still on screen

      if (!view.started) {
        setStage('idle', 'MENUNGGU RONDE', '', 'Tekan Mulai di halaman operator.', '', '');
        return;
      }

      // A verdict nobody has seen yet beats everything else. A rejected turn
      // stays in JUDGING for the hold, so this must come before the check below.
      if (view.last && view.last.seq > shownSeq) {
        var waited = processingAt ? now - processingAt : MIN_PROCESSING_MS;
        if (waited < MIN_PROCESSING_MS) {
          if (!processingAt) processingAt = now;
          paintProcessing();
          setTimeout(paint, MIN_PROCESSING_MS - waited + 20);
          return;
        }
        showVerdict(view.last);
        return;
      }

      if (view.turn_phase === 'JUDGING') {
        if (!processingAt) processingAt = now;
        paintProcessing();
        return;
      }
      processingAt = 0;
      paintTyping();
    }

    function render(next) {
      var first = view === null;
      view = next;
      // A projector that reloads mid-game shows the current state, it does not
      // replay the last verdict.
      if (first) shownSeq = view.last ? view.last.seq : -1;
      paint();
    }

    connect('/ws/screen', render);
  }

  var page = document.body.dataset.page;
  if (page === 'team') initTeam();
  else if (page === 'admin') initAdmin();
  else if (page === 'screen') initScreen();
})();
