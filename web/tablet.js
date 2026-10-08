(function () {
    'use strict';

    const elements = {
        audio: document.getElementById('audio'),
        video: document.getElementById('video'),
        status: document.getElementById('status'),
        statusText: document.getElementById('statusText'),
        startLayer: document.getElementById('startLayer'),
        startButton: document.getElementById('startButton'),
        startHint: document.getElementById('startHint'),
        reconnectButton: document.getElementById('reconnectButton'),
        fullscreenButton: document.getElementById('fullscreenButton'),
        fullscreenIcon: document.getElementById('fullscreenIcon'),
        micButton: document.getElementById('micButton'),
        micLabel: document.getElementById('micLabel'),
        interruptButton: document.getElementById('interruptButton'),
        transcript: document.getElementById('transcript'),
        toast: document.getElementById('toast'),
        asrFrame: document.getElementById('asrFrame'),
    };

    const params = new URLSearchParams(window.location.search);
    let peerConnection = null;
    let sessionId = null;
    let connecting = false;
    let micPaused = false;
    let toastTimer = null;
    let transcriptTimer = null;

    function setStatus(text, state) {
        elements.statusText.textContent = text;
        elements.status.dataset.state = state || 'idle';
    }

    function showToast(message, duration) {
        window.clearTimeout(toastTimer);
        elements.toast.textContent = message;
        elements.toast.classList.add('visible');
        toastTimer = window.setTimeout(() => elements.toast.classList.remove('visible'), duration || 4000);
    }

    function showTranscript(text) {
        window.clearTimeout(transcriptTimer);
        elements.transcript.textContent = text;
        elements.transcript.classList.toggle('visible', Boolean(text));
        if (text) {
            transcriptTimer = window.setTimeout(() => elements.transcript.classList.remove('visible'), 8000);
        }
    }

    function updateMicButton(paused) {
        micPaused = paused;
        elements.micButton.classList.toggle('paused', paused);
        elements.micLabel.textContent = paused ? '继续对话' : '聆听中';
        elements.micButton.setAttribute('aria-pressed', String(!paused));
    }

    function asrCommand(action) {
        if (!elements.asrFrame.contentWindow) return;
        elements.asrFrame.contentWindow.postMessage({ source: 'tablet-ui', action }, window.location.origin);
    }

    function buildOfferPayload(offer) {
        const payload = { sdp: offer.sdp, type: offer.type };
        const optionalParams = {
            avatar: params.get('avatar'),
            refaudio: params.get('refaudio'),
            reftext: params.get('reftext'),
        };
        Object.entries(optionalParams).forEach(([key, value]) => {
            if (value) payload[key] = value;
        });
        return payload;
    }

    function waitForIceGathering(pc) {
        if (pc.iceGatheringState === 'complete') return Promise.resolve();
        return new Promise((resolve) => {
            const check = () => {
                if (pc.iceGatheringState !== 'complete') return;
                pc.removeEventListener('icegatheringstatechange', check);
                resolve();
            };
            pc.addEventListener('icegatheringstatechange', check);
        });
    }

    function cleanupConnection() {
        elements.asrFrame.removeAttribute('src');
        elements.video.srcObject = null;
        elements.audio.srcObject = null;
        sessionId = null;
        if (peerConnection) {
            peerConnection.ontrack = null;
            peerConnection.onconnectionstatechange = null;
            peerConnection.close();
            peerConnection = null;
        }
        elements.micButton.disabled = true;
        elements.interruptButton.disabled = true;
        updateMicButton(false);
    }

    async function connect() {
        if (connecting) return;
        connecting = true;
        cleanupConnection();
        elements.startButton.disabled = true;
        elements.startHint.textContent = '正在连接数字人';
        setStatus('连接中', 'idle');

        const rtcConfig = { sdpSemantics: 'unified-plan' };
        if (params.get('stun') === '1') {
            rtcConfig.iceServers = [{ urls: 'stun:stun.l.google.com:19302' }];
        }

        try {
            const pc = new RTCPeerConnection(rtcConfig);
            peerConnection = pc;
            pc.addTransceiver('video', { direction: 'recvonly' });
            pc.addTransceiver('audio', { direction: 'recvonly' });
            pc.addEventListener('track', (event) => {
                const stream = event.streams[0];
                if (event.track.kind === 'video') {
                    elements.video.srcObject = stream;
                    elements.video.play().catch(() => {});
                } else {
                    elements.audio.srcObject = stream;
                    elements.audio.play().catch(() => showToast('轻触屏幕后即可播放声音'));
                }
            });
            pc.addEventListener('connectionstatechange', () => {
                if (pc !== peerConnection) return;
                if (pc.connectionState === 'connected') {
                    setStatus('正在准备麦克风', 'online');
                    elements.startLayer.classList.add('hidden');
                    elements.interruptButton.disabled = false;
                } else if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected') {
                    setStatus('连接已断开', 'error');
                    elements.micButton.disabled = true;
                    showToast('连接中断，请点击右上角重新连接');
                }
            });

            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            await waitForIceGathering(pc);
            const response = await fetch('/offer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(buildOfferPayload(pc.localDescription)),
            });
            if (!response.ok) throw new Error('服务器响应异常 (' + response.status + ')');
            const answer = await response.json();
            if (answer.code && answer.code !== 0) throw new Error(answer.msg || '数字人连接失败');
            if (!answer.sdp || !answer.sessionid) throw new Error('服务器没有返回有效会话');
            sessionId = String(answer.sessionid);
            await pc.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp: answer.sdp }));
            elements.asrFrame.src = '/asr/index.html?kiosk=1&sessionid=' + encodeURIComponent(sessionId);
        } catch (error) {
            cleanupConnection();
            const message = error && error.message ? error.message : String(error);
            setStatus('连接失败', 'error');
            elements.startHint.textContent = message;
            elements.startLayer.classList.remove('hidden');
            showToast(message, 6000);
        } finally {
            connecting = false;
            elements.startButton.disabled = false;
            if (!sessionId) elements.startHint.textContent = '点击重试';
        }
    }

    async function toggleFullscreen() {
        try {
            if (!document.fullscreenElement) {
                await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
                if (screen.orientation && screen.orientation.lock) {
                    screen.orientation.lock('landscape').catch(() => {});
                }
            } else {
                await document.exitFullscreen();
            }
        } catch (error) {
            showToast('当前浏览器不允许全屏，请使用浏览器菜单进入全屏');
        }
    }

    async function ensureMicrophonePermission() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error('当前浏览器不支持麦克风采集');
        }
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track) => track.stop());
    }

    async function startExperience() {
        if (!window.isSecureContext && !['localhost', '127.0.0.1'].includes(window.location.hostname)) {
            const httpsUrl = 'https://' + window.location.hostname + ':8443/tablet.html';
            elements.startHint.textContent = '先安装本机 CA 证书，再访问 ' + httpsUrl;
            showToast('安卓 Chrome 需要可信 HTTPS 才能使用麦克风', 6000);
            return;
        }
        elements.startButton.disabled = true;
        elements.startHint.textContent = '正在请求麦克风权限';
        try {
            await ensureMicrophonePermission();
        } catch (error) {
            const denied = error && (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError');
            const message = denied
                ? '麦克风权限被拒绝，请在浏览器站点设置中允许麦克风'
                : (error && error.message ? error.message : '无法打开麦克风');
            elements.startButton.disabled = false;
            elements.startHint.textContent = message;
            setStatus('麦克风不可用', 'error');
            showToast(message, 6000);
            return;
        }
        toggleFullscreen();
        await connect();
    }

    async function interruptTalk() {
        if (!sessionId) return;
        try {
            const response = await fetch('/interrupt_talk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sessionid: sessionId }),
            });
            const result = await response.json();
            if (result.code !== 0) throw new Error(result.msg || '打断失败');
            setStatus(micPaused ? '麦克风已暂停' : '请讲话', micPaused ? 'online' : 'listening');
        } catch (error) {
            showToast(error.message || '打断失败');
        }
    }

    function handleAsrMessage(event) {
        if (event.origin !== window.location.origin || !event.data || event.data.source !== 'livetalking-asr') return;
        const data = event.data;
        if (data.type === 'state') {
            const stateMap = {
                connecting: ['正在连接语音识别', 'online'],
                ready: ['麦克风准备中', 'online'],
                listening: ['请讲话', 'listening'],
                recognizing: ['正在识别', 'online'],
                waiting: ['数字人思考中', 'online'],
                paused: ['麦克风已暂停', 'online'],
                error: ['麦克风不可用', 'error'],
            };
            const status = stateMap[data.state] || [data.message || '语音服务状态更新', 'online'];
            setStatus(data.message || status[0], status[1]);
            elements.micButton.disabled = data.state === 'connecting' || data.state === 'error';
            if (data.state === 'listening') updateMicButton(false);
            if (data.state === 'paused') updateMicButton(true);
            if (data.state === 'error' && data.message) showToast(data.message, 6000);
        } else if (data.type === 'transcript') {
            showTranscript(data.text || '');
        }
    }

    elements.startButton.addEventListener('click', startExperience);
    elements.reconnectButton.addEventListener('click', connect);
    elements.fullscreenButton.addEventListener('click', toggleFullscreen);
    elements.interruptButton.addEventListener('click', interruptTalk);
    elements.micButton.addEventListener('click', () => {
        const action = micPaused ? 'resume' : 'pause';
        asrCommand(action);
        updateMicButton(!micPaused);
    });
    document.addEventListener('fullscreenchange', () => {
        const active = Boolean(document.fullscreenElement);
        elements.fullscreenButton.setAttribute('aria-label', active ? '退出全屏' : '进入全屏');
        elements.fullscreenButton.title = active ? '退出全屏' : '进入全屏';
    });
    window.addEventListener('message', handleAsrMessage);
    window.addEventListener('beforeunload', cleanupConnection);
})();
