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
  function connect(path, onMessage) {
    var socket = null;
    var queue = [];

    function open() {
      socket = new WebSocket(socketPath(path));
      socket.onopen = function () {
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

    function statusFor(view) {
      if (view.round_over) {
        return view.winner === teamId ? 'Tim kami menang!' : 'Ronde selesai';
      }
      if (view.paused) return 'Ronde dijeda';
      if (view.phase === 'IDLE') return 'Menunggu ronde dimulai';
      if (view.phase === 'TYPING') {
        if (view.last && view.last.status === 'REJECT') return 'Ditolak ✘ ' + view.last.message;
        if (view.used >= view.max_players - 1) return 'Instruksi terakhir!';
        return 'Giliranmu! Ketik satu instruksi';
      }
      if (view.phase === 'JUDGING') return 'Memproses...';
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
      var text = els.input.value.trim();
      if (!text || !state || state.phase !== 'TYPING') return;
      link.send({ type: 'submit', text: text });
      els.input.value = '';
      els.input.disabled = true;
      els.send.disabled = true;
      els.status.textContent = 'Memproses...';
      els.status.className = 'status-line';
    });

    var link = connect('/ws/team/' + teamId, render);
  }

  /* ----------------------------------------------------------------- admin */

  function initAdmin() {
    var state = null;
    var keyA = localStorage.getItem('eb_key_a') || 'a';
    var keyB = localStorage.getItem('eb_key_b') || 'l';

    var els = {
      clock: $('#round-clock'),
      columns: $('#columns'),
      modeSwitch: $('#mode-switch'),
      start: $('#btn-start'),
      pause: $('#btn-pause'),
      resume: $('#btn-resume'),
      reset: $('#btn-reset'),
      mode: $('#f-mode'),
      ai: $('#f-ai'),
      model: $('#f-model'),
      latency: $('#f-latency'),
      devA: $('#f-dev-a'),
      devB: $('#f-dev-b'),
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
        '<div class="last-box">' +
          '<div class="cmd" data-role="last-text">Belum ada instruksi.</div>' +
          '<div class="meta" data-role="last-meta"></div>' +
        '</div>' +
        '<div class="big-buttons">' +
          '<button class="primary" data-action="done">Selesai</button>' +
          '<button data-action="redo">Ulang</button>' +
        '</div>' +
        '<div class="force-row">' +
          '<button data-action="force-accept">Paksa terima</button>' +
          '<button data-action="force-reject">Paksa tolak</button>' +
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
      root.querySelector('[data-action="done"]').disabled = !locked;
      root.querySelector('[data-action="redo"]').disabled = !locked;
      root.querySelector('[data-action="force-accept"]').disabled = !pending;
      root.querySelector('[data-action="force-reject"]').disabled = !pending;

      var last = team.last;
      setText(root, 'last-text', last ? last.text || '(kosong)' : 'Belum ada instruksi.');
      var meta = [];
      if (last) meta.push(last.status + ' · ' + last.message);
      if (team.source) meta.push('sumber: ' + team.source);
      if (team.latency_ms != null) meta.push(Math.round(team.latency_ms) + ' ms');
      if (team.command) meta.push(team.command.verb);
      if (team.pending_text) meta.push('menunggu operator');
      setText(root, 'last-meta', meta.join('  ·  '));
    }

    function render(view) {
      state = view;
      els.clock.textContent = fmtClock(view.round_seconds_left);
      els.mode.textContent = view.judge_mode;
      els.ai.textContent = view.ai_available ? 'aktif' : 'mati (kata kunci)';
      els.model.textContent = view.ai_model || '-';

      Array.prototype.forEach.call(els.modeSwitch.children, function (button) {
        button.className = button.dataset.mode === view.judge_mode ? 'active' : '';
      });

      TEAM_IDS.forEach(function (teamId) {
        renderTeam(teamId, view.teams[teamId]);
      });

      var latency = view.teams.a.ai_latency_ms || view.teams.b.ai_latency_ms;
      var fallback = view.teams.a.fallback_used || view.teams.b.fallback_used;
      els.latency.innerHTML = latency != null
        ? (fallback ? '<span class="dot bad"></span>' : '<span class="dot ok"></span>') +
          Math.round(latency) + ' ms' + (fallback ? ' (fallback)' : '')
        : '<span class="dot"></span>-';

      [['a', els.devA], ['b', els.devB]].forEach(function (pair) {
        var team = view.teams[pair[0]];
        var dotClass = team.device === 'connected' ? 'ok' : (team.device === 'error' ? 'bad' : 'warn');
        pair[1].innerHTML = '<span class="dot ' + dotClass + '"></span>' + team.device +
          (team.device_port ? ' ' + team.device_port : '');
      });

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
    });

    els.modeSwitch.addEventListener('click', function (event) {
      var button = event.target.closest('button[data-mode]');
      if (!button) return;
      link.send({ type: 'set_judge_mode', mode: button.dataset.mode });
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

    var link = connect('/ws/admin', render);
  }

  /* ---------------------------------------------------------------- screen */

  function initScreen() {
    var container = $('#teams');
    var clock = $('#round-clock');
    var winner = $('#winner');

    TEAM_IDS.forEach(function (teamId) {
      var card = document.createElement('section');
      card.className = 'screen-team';
      card.dataset.team = teamId;
      card.innerHTML =
        '<h2>' + (teamId === 'a' ? 'TIM A' : 'TIM B') + '</h2>' +
        '<div class="big" data-role="progress">0/7</div>' +
        '<div class="progress"><span data-role="bar"></span></div>' +
        '<div class="sub" data-role="left">14 instruksi tersisa</div>' +
        '<div class="msg" data-role="msg"></div>';
      container.appendChild(card);
    });

    function render(view) {
      clock.textContent = fmtClock(view.round_seconds_left);
      TEAM_IDS.forEach(function (teamId) {
        var team = view.teams[teamId];
        var card = container.querySelector('[data-team="' + teamId + '"]');
        card.querySelector('[data-role="progress"]').textContent =
          team.milestones + '/' + MILESTONES;
        card.querySelector('[data-role="bar"]').style.width =
          Math.round((team.milestones / MILESTONES) * 100) + '%';
        card.querySelector('[data-role="left"]').textContent =
          team.remaining + ' instruksi tersisa';
        var message = card.querySelector('[data-role="msg"]');
        message.textContent = team.last ? team.last.message : '';
        message.className = 'msg ' + (team.last ? team.last.status.toLowerCase() : '');
      });

      if (view.round_over) {
        winner.hidden = false;
        winner.textContent = view.winner
          ? (view.winner === 'a' ? 'TIM A MENANG' : 'TIM B MENANG')
          : 'SERI';
      } else {
        winner.hidden = true;
      }
    }

    connect('/ws/screen', render);
  }

  var page = document.body.dataset.page;
  if (page === 'team') initTeam();
  else if (page === 'admin') initAdmin();
  else if (page === 'screen') initScreen();
})();
