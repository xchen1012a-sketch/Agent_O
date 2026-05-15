import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

var _DH_ENABLED_PAGES = ['practical_training', 'on_duty_assistant', 'knowledge_qa', 'home', 'login', 'assessment', 'training_path', 'competition', 'module_ability', 'growth_plan', 'quick_query', 'talent_dashboard', 'theory_learning'];

var _PAGE_GREETINGS = {
  home: '欢迎回来，{displayName}，今天也要加油哦！',
  on_duty_assistant: '在岗助手已就绪，遇到顾客异议随时问我。',
  knowledge_qa: '知识问答已就绪，有什么不懂的尽管问。',
  practical_training: '智能陪练已就绪，点击我开始练习。',
  login: '你好！欢迎使用珠宝企培数智管家。',
  assessment: '考试已就绪，深呼吸，相信自己的准备。',
  training_path: '通关训练已就绪，今天也要全力以赴！',
  competition: '排行榜已更新，来看看你的排名吧。',
  module_ability: '能力图谱已加载，我来帮你分析强弱项。',
  growth_plan: '成长计划已就绪，让我们一起规划学习路径。',
  quick_query: '数据查询已就绪，输入你的问题我来解读。',
  talent_dashboard: '风险看板已就绪，让我来播报关键指标。',
  theory_learning: '理论学习区已就绪，打开文档我来陪你一起看。',
};

function _shouldAutoPageGreeting(pageId) {
  var page = String(pageId || '').trim();
  if (!page) return false;
  // Business pages already emit their own coaching/result copy. Auto greeting
  // there makes the avatar surface two messages back to back.
  return page === 'home';
}

function _calcFanPositions(count) {
  var radius = 7;
  var w = window.innerWidth;
  if (w <= 640) radius = 4.5;
  else if (w <= 1023) radius = 5.5;
  var startAngle = 240;
  var sweep = 120;
  var positions = [];
  for (var i = 0; i < count; i++) {
    var fraction = count > 1 ? i / (count - 1) : 0.5;
    var angleDeg = startAngle - fraction * sweep;
    var angleRad = angleDeg * Math.PI / 180;
    positions.push({
      x: (Math.cos(angleRad) * radius).toFixed(3),
      y: (-Math.sin(angleRad) * radius).toFixed(3),
      delay: (i * 50)
    });
  }
  return positions;
}

function _getMenuItems(pageId) {
  var poseItem = { label: '切换动作', action: 'cyclePose', icon: _menuIconCycle() };
  var resetItem = { label: '恢复视角', action: 'resetView', icon: _menuIconReset() };
  var autoVoiceItem = _getAutoVoiceMenuItem();
  var common = [
    poseItem,
    autoVoiceItem,
    { label: '新会话', action: 'newSession', icon: _menuIconPlus() },
  ];
  if (pageId === 'practical_training') {
    return [
      poseItem,
      autoVoiceItem,
      { label: '播放点评', action: 'replaySummary', icon: _menuIconReplay() },
      resetItem,
    ];
  }
  if (pageId === 'on_duty_assistant') {
    return common.concat([
      { label: '读出建议', action: 'readReply', icon: _menuIconReplay() },
      resetItem,
    ]);
  }
  if (pageId === 'knowledge_qa') {
    return common.concat([
      { label: '读出回答', action: 'readReply', icon: _menuIconReplay() },
      resetItem,
    ]);
  }
  if (pageId === 'home') {
    return [
      poseItem,
      autoVoiceItem,
      { label: '今日播报', action: 'dailyBrief', icon: _menuIconReplay() },
      resetItem,
    ];
  }
  if (pageId === 'login') {
    return [poseItem, autoVoiceItem, resetItem];
  }
  if (pageId === 'assessment') {
    return [
      poseItem,
      autoVoiceItem,
      { label: '考前鼓励', action: 'examEncourage', icon: _menuIconReplay() },
      { label: '答题提醒', action: 'replaySummary', icon: _menuIconPlus() },
      resetItem,
    ];
  }
  if (pageId === 'training_path') {
    return [
      poseItem,
      autoVoiceItem,
      { label: '播报进度', action: 'trainingProgress', icon: _menuIconReplay() },
      resetItem,
    ];
  }
  if (pageId === 'competition') {
    return [
      poseItem,
      autoVoiceItem,
      { label: '排行解说', action: 'narrateLeaderboard', icon: _menuIconReplay() },
      resetItem,
    ];
  }
  if (pageId === 'module_ability' || pageId === 'growth_plan') {
    return [
      poseItem,
      autoVoiceItem,
      { label: '能力点评', action: 'skillComment', icon: _menuIconReplay() },
      resetItem,
    ];
  }
  if (pageId === 'quick_query') {
    return [
      poseItem,
      autoVoiceItem,
      { label: '新会话', action: 'newSession', icon: _menuIconPlus() },
      { label: '读出结果', action: 'narrateQuery', icon: _menuIconReplay() },
      resetItem,
    ];
  }
  if (pageId === 'talent_dashboard') {
    return [
      poseItem,
      autoVoiceItem,
      { label: '风险简报', action: 'riskBrief', icon: _menuIconReplay() },
      resetItem,
    ];
  }
  if (pageId === 'theory_learning') {
    return [
      poseItem,
      autoVoiceItem,
      { label: '回忆挑战', action: 'recallQuiz', icon: _menuIconReplay() },
      resetItem,
    ];
  }
  return common.concat([resetItem]);
}

var _POSE_ORDER = ['standby', 'agree', 'celebrate', 'encourage', 'think'];

var _POSE_DISPLAY_NAMES = {
  standby: '待机', agree: '赞同', celebrate: '庆祝', encourage: '鼓励',
  think: '思考', idle_hint: '闲置提示', slight_nod: '微微点头',
  slight_tilt: '微微歪头', pointing: '指引', reading: '阅读'
};

var _PAGE_ACTION_ITEMS = {
  practical_training: [{ action: 'replaySummary', label: '播放点评' }],
  on_duty_assistant: [{ action: 'readReply', label: '读出建议' }],
  knowledge_qa: [{ action: 'readReply', label: '读出回答' }],
  home: [{ action: 'dailyBrief', label: '今日播报' }],
  assessment: [{ action: 'examEncourage', label: '考前鼓励' }],
  training_path: [{ action: 'trainingProgress', label: '播报进度' }],
  competition: [{ action: 'narrateLeaderboard', label: '排行解说' }],
  module_ability: [{ action: 'skillComment', label: '能力点评' }],
  growth_plan: [{ action: 'skillComment', label: '能力点评' }],
  quick_query: [{ action: 'narrateQuery', label: '读出结果' }],
  talent_dashboard: [{ action: 'riskBrief', label: '风险简报' }],
  theory_learning: [{ action: 'recallQuiz', label: '回忆挑战' }]
};

var _POSE_PRESETS = {
  standby: {
    avatarRoot: { position: [0, 0, 0], rotation: [0, 0.02, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [0.02, 0, 0] },
    headGroup: { position: [0, 0, 0], rotation: [0.04, 0.16, 0] },
    leftArmGroup: { position: [0, 0, 0], rotation: [0.08, 0.02, -0.08] },
    rightArmGroup: { position: [0, 0, 0], rotation: [0.02, -0.02, 0.08] },
  },
  agree: {
    avatarRoot: { position: [0, -0.02, 0], rotation: [0, 0.02, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [0.08, 0, 0] },
    headGroup: { position: [0, 0, 0], rotation: [0.35, 0.12, 0] },
    leftArmGroup: { position: [0, 0, 0], rotation: [0.15, 0.1, -0.12] },
    rightArmGroup: { position: [0, 0, 0], rotation: [0.15, -0.1, 0.12] },
  },
  celebrate: {
    avatarRoot: { position: [0, -0.03, 0], rotation: [0, 0.04, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [-0.06, 0, 0] },
    headGroup: { position: [0, 0.02, 0], rotation: [0.18, 0, 0] },
    leftArmGroup: { position: [0, 0.05, 0], rotation: [0.6, 0.2, -0.8] },
    rightArmGroup: { position: [0, 0.05, 0], rotation: [0.6, -0.2, 0.8] },
  },
  encourage: {
    avatarRoot: { position: [0, -0.01, 0], rotation: [0, -0.04, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [0.06, 0, 0] },
    headGroup: { position: [0, 0, 0], rotation: [0.25, 0.08, 0] },
    leftArmGroup: { position: [0, 0, 0], rotation: [0.2, 0.1, -0.15] },
    rightArmGroup: { position: [0, 0, 0], rotation: [0.05, -0.05, 0.1] },
  },
  think: {
    avatarRoot: { position: [0, 0, 0], rotation: [0, 0.02, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [0.04, 0.03, 0] },
    headGroup: { position: [0, 0, 0], rotation: [0.1, -0.1, 0.15] },
    leftArmGroup: { position: [0, 0, 0], rotation: [0.1, 0.05, -0.1] },
    rightArmGroup: { position: [0, 0.05, 0.02], rotation: [0.4, -0.3, 0.3] },
  },
  idle_hint: {
    avatarRoot: { position: [0, 0, 0], rotation: [0, -0.06, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [0.03, 0, 0] },
    headGroup: { position: [0, 0, 0], rotation: [-0.05, 0.2, 0.1] },
    leftArmGroup: { position: [0, 0, 0], rotation: [0.15, 0.05, -0.2] },
    rightArmGroup: { position: [0, 0, 0], rotation: [0.05, -0.05, 0.1] },
  },
  slight_nod: {
    avatarRoot: { position: [0, 0, 0], rotation: [0, 0, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [0.02, 0, 0] },
    headGroup: { position: [0, 0, 0], rotation: [0.12, 0, 0] },
    leftArmGroup: { position: [0, 0, 0], rotation: [0, 0, 0] },
    rightArmGroup: { position: [0, 0, 0], rotation: [0, 0, 0] },
  },
  slight_tilt: {
    avatarRoot: { position: [0, 0, 0], rotation: [0, 0, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [0, 0, 0] },
    headGroup: { position: [0, 0, 0], rotation: [0.04, 0.08, 0.12] },
    leftArmGroup: { position: [0, 0, 0], rotation: [0, 0, 0] },
    rightArmGroup: { position: [0, 0, 0], rotation: [0, 0, 0] },
  },
  pointing: {
    avatarRoot: { position: [0, -0.01, 0], rotation: [0, -0.06, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [0.04, 0, 0] },
    headGroup: { position: [0, 0, 0], rotation: [0.1, -0.12, 0.05] },
    leftArmGroup: { position: [0, 0, 0], rotation: [0.08, 0.04, -0.06] },
    rightArmGroup: { position: [0, 0.03, 0.04], rotation: [0.5, -0.4, 0.2] },
  },
  reading: {
    avatarRoot: { position: [0, 0, 0], rotation: [0, 0.02, 0] },
    upperBodyGroup: { position: [0, 0, 0], rotation: [0.06, 0, 0] },
    headGroup: { position: [0, -0.02, 0], rotation: [0.15, 0.08, 0] },
    leftArmGroup: { position: [0, 0, 0], rotation: [0.3, 0.15, -0.2] },
    rightArmGroup: { position: [0, 0, 0], rotation: [0.1, -0.05, 0.08] },
  },
};

var _EMOTION_BUBBLES = {
  celebrate: ['太棒了！继续保持！', '表现优秀！', '做得很好！'],
  encourage: ['没关系，再试试看！', '加油，你可以的！', '别灰心，下次一定行！'],
  idle_hint: ['需要帮助吗？', '在想什么呢？', '我在这里等你哦'],
};

var _IDLE_THRESHOLD_MS = 90000;
var _IDLE_COOLDOWN_MS = 180000;
var _IDLE_CHECK_INTERVAL_MS = 30000;
var _MICRO_EXPRESSION_DURATION_MS = 1500;

var _MICRO_POSITIVE_WORDS = ['很好', '不错', '正确', '优秀', '完美', '非常好', '对的', '棒', '厉害', '好的', '当然', '没问题', '可以'];
var _MICRO_NEGATIVE_WORDS = ['不对', '错误', '重新', '再想想', '不完全', '不太对', '抱歉', '不好意思', '可能不太'];
var _MICRO_THINKING_WORDS = ['让我想想', '这个问题', '嗯', '稍等', '考虑', '分析', '判断'];

/* ====== Speaking Micro-Interaction Config ====== */

var _SPEAKING_MICRO = {
  breatheAmp: 0.012,
  breatheSpeed: 2.8,
  swayAmp: 0.008,
  swaySpeed: 0.7,
  headBobAmp: 0.025,
  headBobSpeed: 1.5,
  gestureMinMs: 2500,
  gestureMaxMs: 5000,
  gestureDurMs: 800,
};

var _SPEAKING_GESTURES = [
  { headPitch: 0.12, headYaw: 0, bodyPitch: 0.02, leftArmRotX: 0, rightArmRotX: 0 },
  { headPitch: 0.04, headYaw: -0.06, bodyPitch: 0, leftArmRotX: -0.08, rightArmRotX: 0 },
  { headPitch: 0.04, headYaw: 0.06, bodyPitch: 0, leftArmRotX: 0, rightArmRotX: 0.08 },
  { headPitch: 0.03, headYaw: 0.04, bodyPitch: 0, leftArmRotX: -0.2, rightArmRotX: 0 },
  { headPitch: 0.03, headYaw: -0.04, bodyPitch: 0, leftArmRotX: 0, rightArmRotX: 0.2 },
  { headPitch: 0.08, headYaw: 0, bodyPitch: 0.04, leftArmRotX: -0.12, rightArmRotX: 0.12 },
];

var _state = {
  loaded: false,
  speaking: false,
  visible: true,
  practiceActive: false,
  menuOpen: false,
  sidePanelOpen: false,
  bubbleKind: 'score',
  currentSpeechPriority: 0,
  visibleBubblePriority: 0,
  activePage: '',
  greetedPages: {},
  pendingGreeting: null,
  modelUrl: './vendor/models/3D.glb',
  lastSummary: '',
  audioEl: null,
  audioUrl: '',
  browserUtterance: null,
  speechRequestToken: 0,
  activeSpeechRequestToken: 0,
  mediaSource: null,
  streamReader: null,
  streamAbort: null,
  portalRoot: null,
  canvasWrap: null,
  resizeObserver: null,
  animFrameId: null,
  speechTimeoutId: null,
  listenersBound: false,
  loadToken: 0,
  scene: null,
  camera: null,
  renderer: null,
  avatar: null,
  clock: null,
  mixer: null,
  animClips: null,
  currentAnimIdx: 0,
  poseGroups: null,
  poseBases: null,
  currentPoseName: 'standby',
  targetPoseName: 'standby',
  onDocumentClick: null,
  onDocumentKeydown: null,
  onVisibilityChange: null,
  onMenuButtonClick: null,
  lastActivityTime: Date.now(),
  idleCheckInterval: null,
  lastIdleHintTime: 0,
  previousPoseName: 'standby',
  emotionTimeoutId: null,
  pendingEmotion: null,
  microExpressionTimeoutId: null,
  preMicroPoseName: null,
  speakingMicro: {
    active: false,
    startTime: 0,
    nextGestureTime: 0,
    currentGesture: null,
    gestureStartTime: 0,
  },
  mouseFollow: {
    enabled: false,
    targetHeadYaw: 0,
    targetHeadPitch: 0,
    targetBodyYaw: 0,
    headYaw: 0,
    headPitch: 0,
    bodyYaw: 0,
    onMove: null,
    bonesFound: false,
    headBone: null,
    neckBone: null,
    spineBone: null,
    jawBone: null,
  },
  mouthMorphs: null,
  dragRotate: {
    active: false,
    moved: false,
    startX: 0,
    startY: 0,
    startRotY: 0,
    startRotX: 0,
  },
  avatarHomeRotation: { x: 0, y: -Math.PI / 2, z: 0 },
  onDragStart: null,
  onDragMove: null,
  onDragEnd: null,
};

function renderDigitalHumanWidget() {
  return _buildWidgetMarkup();
}

function _getPreferenceSnapshot() {
  if (typeof window !== 'undefined' && typeof window.getDigitalHumanPreferences === 'function') {
    try {
      return window.getDigitalHumanPreferences() || {};
    } catch (e) {}
  }
  return {};
}

function _isPreferenceEnabled(key) {
  var prefs = _getPreferenceSnapshot();
  return prefs[String(key || '').trim()] !== false;
}

function digitalHumanSyncPreferences() {
  if (!_state.practiceActive) return;
  _syncAutoVoiceUi();
  if (_isPreferenceEnabled('mouse_follow')) _enableMouseFollow();
  else _disableMouseFollow();
  if (_isPreferenceEnabled('idle_hint')) _startIdleCheck();
  else _stopIdleCheck();
  _syncSidePanelSettings();
}

function mountDigitalHuman() {
  if (typeof window.isDigitalHumanSystemEnabled === 'function' && !window.isDigitalHumanSystemEnabled()) {
    destroyDigitalHuman();
    return;
  }
  var root = _ensurePortalRoot();
  var wrap = document.getElementById('dh-canvas-wrap');
  if (!root || !wrap) return;

  _state.practiceActive = true;
  _state.visible = !document.hidden;
  _state.portalRoot = root;
  _state.canvasWrap = wrap;
  root.hidden = false;

  // Sync login-mode CSS class (setActivePage may run before portalRoot exists)
  if (_state.activePage === 'login') {
    root.classList.add('dh-login-mode');
  } else {
    root.classList.remove('dh-login-mode');
  }

  _bindEvents();
  _closeQuickMenu();
  digitalHumanSyncPreferences();
  _bindLoginInteractions();

  if (!_state.renderer) {
    _state.loadToken += 1;
    _initScene(wrap);
    _loadModel(_state.loadToken);
  } else if (_state.renderer.domElement.parentNode !== wrap) {
    wrap.appendChild(_state.renderer.domElement);
    _refreshSceneSize();
    _applyCurrentPose(true);
  } else {
    _refreshSceneSize();
    _applyCurrentPose(true);
  }

  _syncPoseDataset();
  _startRenderLoop();
}

function destroyDigitalHuman() {
  _state.practiceActive = false;
  _state.loadToken += 1;

  _unbindLoginInteractions();
  _disableMouseFollow();
  digitalHumanStopSpeech({ preserveBubble: true });
  _showError('');
  _closeQuickMenu();
  _closeSidePanel();
  _unbindEvents();
  _disconnectResizeObserver();

  if (_state.portalRoot) {
    _state.portalRoot.classList.remove('dh-login-mode');
  }

  if (_state.animFrameId) {
    cancelAnimationFrame(_state.animFrameId);
    _state.animFrameId = null;
  }

  if (_state.renderer && _state.renderer.domElement && _state.renderer.domElement.parentNode) {
    _state.renderer.domElement.parentNode.removeChild(_state.renderer.domElement);
  }

  if (_state.renderer) {
    _state.renderer.dispose();
    _state.renderer = null;
  }

  if (_state.scene) {
    _state.scene.traverse(function (obj) {
      if (obj.geometry) obj.geometry.dispose();
      if (!obj.material) return;
      if (Array.isArray(obj.material)) {
        obj.material.forEach(function (material) {
          if (material && typeof material.dispose === 'function') material.dispose();
        });
      } else if (typeof obj.material.dispose === 'function') {
        obj.material.dispose();
      }
    });
  }

  _state.scene = null;
  _state.camera = null;
  _state.avatar = null;
  _state.clock = null;
  _state.poseGroups = null;
  _state.poseBases = null;
  if (_state.mixer) {
    _state.mixer.stopAllAction();
    _state.mixer = null;
  }
  _state.animClips = null;
  _state.mouthMorphs = null;
  _state.currentAnimIdx = 0;
  _state.loaded = false;
  _state.menuOpen = false;
  _state.canvasWrap = null;
  _state.currentPoseName = 'standby';
  _state.targetPoseName = 'standby';

  if (_state.portalRoot && _state.portalRoot.parentNode) {
    _state.portalRoot.parentNode.removeChild(_state.portalRoot);
  }
  _state.portalRoot = null;
}

function digitalHumanSpeak(text, options) {
  var nextText = String(text || '').trim();
  var opts = options && typeof options === 'object' ? options : {};
  var priority = _speechPriorityForOptions(opts);
  if (!nextText) return;
  if (typeof window.isDigitalHumanSystemEnabled === 'function' && !window.isDigitalHumanSystemEnabled()) return;
  if (!_shouldInterruptForPriority(priority)) return;

  if (String(opts.scene || '').trim() !== 'greeting' && opts.bubbleKind !== 'greeting') {
    _state.pendingGreeting = null;
  }

  digitalHumanStopSpeech({ preserveBubble: true, preserveRequestToken: true });
  _showError('');

  _state.lastSummary = nextText;
  _setBubble(nextText, opts.bubbleKind || 'score', priority);

  if (opts.pose) {
    digitalHumanSetPose(opts.pose);
  }

  if (!_shouldGenerateVoice(opts)) {
    _state.speaking = false;
    _state.currentSpeechPriority = 0;
    _setSpeechActionVisible(false);
    _scheduleBubbleHide(3500);
    return;
  }

  var requestToken = _beginSpeechRequest(priority);
  _state.speaking = true;
  _startSpeakingMicro();
  _setSpeechActionVisible(true);
  if (window.__dhAudioTestState) {
    _playAudio(new Blob(['dh-audio-test'], { type: 'audio/mpeg' }), requestToken);
    return;
  }
  if (_shouldUseBrowserTts()) {
    _speakWithBrowserTts(nextText, requestToken);
    return;
  }
  var ttsPayload = { text: nextText };
  if (opts.emotion) ttsPayload.emotion = opts.emotion;
  if (typeof window.getDigitalHumanMinimaxVoice === 'function') {
    var voiceId = window.getDigitalHumanMinimaxVoice();
    if (voiceId) ttsPayload.voice_id = voiceId;
  }
  // Streaming playback currently causes repeated spoken content in production.
  // Keep the implementation around, but route live speech through the stable
  // non-streaming endpoint until chunk semantics are fixed end to end.
  _playAudioLegacy(ttsPayload, requestToken);
}

function digitalHumanStopSpeech(options) {
  var opts = options && typeof options === 'object' ? options : {};
  if (!opts.preserveRequestToken) _invalidateSpeechRequests();
  if (typeof window.speechSynthesis !== 'undefined' && window.speechSynthesis && typeof window.speechSynthesis.cancel === 'function') {
    try {
      window.speechSynthesis.cancel();
    } catch (e) {}
  }
  _state.browserUtterance = null;
  // Clean up streaming state
  if (_state.streamAbort) {
    try { _state.streamAbort.abort(); } catch (e) {}
    _state.streamAbort = null;
  }
  if (_state.streamReader) {
    try { _state.streamReader.cancel(); } catch (e) {}
    _state.streamReader = null;
  }
  if (_state.mediaSource) {
    try {
      if (_state.mediaSource.readyState === 'open') {
        _state.mediaSource.endOfStream();
      }
    } catch (e) {}
    _state.mediaSource = null;
  }
  if (_state.audioEl) {
    _state.audioEl.onended = null;
    _state.audioEl.onerror = null;
    _state.audioEl.pause();
    _state.audioEl.src = '';
    _state.audioEl = null;
  }
  _clearSpeechTimeout();
  _releaseAudioUrl();
  _state.speaking = false;
  _state.currentSpeechPriority = 0;
  _stopSpeakingMicro();
  _setSpeechActionVisible(false);
  if (!opts.preserveBubble) _clearBubble();
}

function digitalHumanCollapse() {
  _closeQuickMenu();
}

function digitalHumanExpand() {
  if (typeof window.isDigitalHumanSystemEnabled === 'function' && !window.isDigitalHumanSystemEnabled()) return;
  if (!_state.practiceActive) {
    mountDigitalHuman();
    return;
  }
  _openQuickMenu();
}

function digitalHumanSetActivePage(pageId) {
  var prev = _state.activePage;
  _state.activePage = pageId || '';
  // Manage login-mode CSS class based on active page
  if (_state.portalRoot) {
    if (_state.activePage === 'login') {
      _state.portalRoot.classList.add('dh-login-mode');
    } else {
      _state.portalRoot.classList.remove('dh-login-mode');
    }
  }
  _rebuildQuickMenu();
  if (prev !== pageId && _shouldAutoPageGreeting(pageId) && _isPreferenceEnabled('page_greeting') && _PAGE_GREETINGS[pageId] && !_state.greetedPages[pageId]) {
    _state.greetedPages[pageId] = true;
    var _greetingText = _PAGE_GREETINGS[pageId].replace('{displayName}', localStorage.getItem('displayName') || '');
    if (_state.loaded) {
      digitalHumanSpeak(_greetingText, { bubbleKind: 'greeting', silent: true, trigger: 'auto', scene: 'greeting' });
    } else {
        _state.pendingGreeting = { text: _greetingText, bubbleKind: 'greeting', silent: true, trigger: 'auto', scene: 'greeting' };
    }
  }
}

function _rebuildQuickMenu() {
  var menu = document.getElementById('dh-quick-menu');
  if (!menu) return;
  var items = _getMenuItems(_state.activePage || 'practical_training');
  var positions = _calcFanPositions(items.length);
  menu.innerHTML = items.map(function (item, i) {
    var pos = positions[i];
    return '' +
      '<button type="button" class="dh-quick-menu-item" role="menuitem"' +
        ' data-dh-action="' + item.action + '"' +
        ' style="--fan-x:' + pos.x + 'rem;--fan-y:' + pos.y + 'rem;--fan-delay:' + pos.delay + 'ms"' +
        ' title="' + item.label + '">' +
        '<span class="dh-quick-menu-item__icon" aria-hidden="true">' + item.icon + '</span>' +
        '<span class="dh-quick-menu-item__label">' + item.label + '</span>' +
      '</button>';
  }).join('');
  menu.querySelectorAll('.dh-quick-menu-item').forEach(function (el) {
    el.onclick = function (ev) {
      ev.stopPropagation();
      _handleQuickAction(el.getAttribute('data-dh-action'));
    };
  });
  _rebuildSidePanelPageActions();
}

function digitalHumanSetPose(poseName, immediate) {
  var nextPose = _POSE_PRESETS[poseName] ? poseName : 'standby';
  _state.targetPoseName = nextPose;
  if (immediate) {
    _state.currentPoseName = nextPose;
    _applyCurrentPose(true);
  }
  _syncPoseDataset();
}

function digitalHumanCyclePose() {
  if (_state.mixer && _state.animClips && _state.animClips.length > 1) {
    var currentAction = _state.mixer.clipAction(_state.animClips[_state.currentAnimIdx]);
    currentAction.fadeOut(0.3);
    _state.currentAnimIdx = (_state.currentAnimIdx + 1) % _state.animClips.length;
    var nextAction = _state.mixer.clipAction(_state.animClips[_state.currentAnimIdx]);
    nextAction.reset().fadeIn(0.3).play();
    _state.targetPoseName = _POSE_ORDER[_state.currentAnimIdx % _POSE_ORDER.length] || 'standby';
    _state.currentPoseName = _state.targetPoseName;
    _syncPoseDataset();
    return;
  }
  var currentIndex = _POSE_ORDER.indexOf(_state.targetPoseName);
  var nextIndex = currentIndex >= 0 ? (currentIndex + 1) % _POSE_ORDER.length : 0;
  digitalHumanSetPose(_POSE_ORDER[nextIndex]);
}

function _buildWidgetMarkup() {
  return '' +
    '<div id="dh-widget" class="dh-widget" data-pose="standby" aria-label="数字陪练悬浮助手">' +
      '<div id="dh-quick-menu" class="dh-quick-menu" role="menu" aria-hidden="true">' +
        _buildQuickMenuItems() +
      '</div>' +
      '<div id="dh-auto-voice-status" class="dh-auto-voice-status" hidden aria-live="polite"></div>' +
      '<div id="dh-subtitle" class="dh-speech-bubble" data-kind="score" aria-live="polite">' +
        '<div id="dh-subtitle-label" class="dh-speech-bubble__label">评分点评</div>' +
        '<button type="button" id="dh-stop-speech-btn" class="dh-speech-bubble__action" hidden aria-hidden="true" aria-label="停止播报">停止</button>' +
        '<div id="dh-subtitle-text" class="dh-speech-bubble__text"></div>' +
      '</div>' +
      '<div id="dh-error" class="dh-error" aria-live="polite"></div>' +
      '<div id="dh-avatar-stage" class="dh-avatar-stage" aria-label="数字陪练人物">' +
        '<div id="dh-canvas-wrap" class="dh-canvas-wrap">' +
          '</div>' +
        '</div>' +
      '<button type="button" id="dh-sidepanel-toggle" class="dh-sidepanel-toggle" aria-label="控制面板" title="控制面板">' +
        '<span class="dh-sidepanel-toggle__icon">' + _menuIconPanel() + '</span>' +
      '</button>' +
      '</div>' +
    '</div>';
}

function _buildQuickMenuItems() {
  var items = _getMenuItems(_state.activePage || 'practical_training');
  var positions = _calcFanPositions(items.length);
  return items.map(function (item, i) {
    var pos = positions[i];
    return '' +
      '<button type="button" class="dh-quick-menu-item" role="menuitem"' +
        ' data-dh-action="' + item.action + '"' +
        ' style="--fan-x:' + pos.x + 'rem;--fan-y:' + pos.y + 'rem;--fan-delay:' + pos.delay + 'ms"' +
        ' title="' + item.label + '">' +
        '<span class="dh-quick-menu-item__icon" aria-hidden="true">' + item.icon + '</span>' +
        '<span class="dh-quick-menu-item__label">' + item.label + '</span>' +
      '</button>';
  }).join('');
}

function _getAutoVoiceMenuItem() {
  var prefs = _getDigitalHumanPrefs();
  var muted = prefs.voice_muted === true;
  return {
    label: muted ? '取消静音' : '开启静音',
    action: 'toggleAutoVoice',
    icon: muted ? _menuIconMute() : _menuIconVolume(),
  };
}

function _syncAutoVoiceUi() {
  var prefs = _getDigitalHumanPrefs();
  var muted = prefs.voice_muted === true;
  var badge = document.getElementById('dh-auto-voice-status');
  if (badge) {
    badge.hidden = !muted;
    badge.setAttribute('aria-label', muted ? '静音模式' : '');
    badge.setAttribute('title', muted ? '静音模式' : '');
    badge.innerHTML = muted
      ? '<span class="dh-auto-voice-status__icon" aria-hidden="true">' + _menuIconMute() + '</span>'
      : '';
  }
  _rebuildQuickMenu();
}

function _buildSidePanelMarkup() {
  var sectionsHtml = '';

  // Poses section
  var poseItems = Object.keys(_POSE_PRESETS).map(function (name) {
    return { action: 'setPose:' + name, label: _POSE_DISPLAY_NAMES[name] || name };
  });
  sectionsHtml += _buildSidePanelSection('poses', '动作', poseItems);

  // Reactions section
  sectionsHtml += _buildSidePanelSection('reactions', '情绪反应', [
    { action: 'react:celebrate', label: '庆祝' },
    { action: 'react:encourage', label: '鼓励' },
    { action: 'react:agree', label: '赞同' },
    { action: 'react:think', label: '思考' }
  ]);

  // Micro expressions section
  sectionsHtml += _buildSidePanelSection('micro', '微表情', [
    { action: 'micro:positive', label: '积极' },
    { action: 'micro:negative', label: '消极' },
    { action: 'micro:thinking', label: '思考' }
  ]);

  // Speech section
  sectionsHtml += _buildSidePanelSection('speech', '语音控制', [
    { action: 'stopSpeech', label: '停止播报' }
  ]);

  // Settings section (toggles)
  sectionsHtml += _buildSidePanelSettingsSection();

  // Page actions section (dynamically populated)
  var pageItems = _getPageActionItems();
  if (pageItems.length > 0) {
    sectionsHtml += _buildSidePanelSection('page-actions', '页面操作', pageItems);
  }

  // General section
  sectionsHtml += _buildSidePanelSection('general', '通用', [
    { action: 'cyclePose', label: '切换动作' },
    { action: 'resetView', label: '恢复视角' },
    { action: 'newSession', label: '新会话' }
  ]);

  return '' +
    '<div id="dh-sidepanel" class="dh-sidepanel" aria-hidden="true">' +
      '<div class="dh-sp-header">' +
        '<span class="dh-sp-header__title">数字人控制</span>' +
        '<button type="button" id="dh-sidepanel-close" class="dh-sp-header__close" aria-label="关闭">' +
          _menuIconClose() +
        '</button>' +
      '</div>' +
      '<div id="dh-sidepanel-body" class="dh-sp-body">' +
        sectionsHtml +
      '</div>' +
    '</div>';
}

function _buildSidePanelSection(sectionId, title, items) {
  if (!items || items.length === 0) return '';
  var gridHtml = items.map(function (item) {
    return '<button type="button" class="dh-sp-btn" data-dh-sp-action="' + item.action + '">' +
      item.label +
    '</button>';
  }).join('');
  return '' +
    '<div class="dh-sp-section" data-dh-sp-section="' + sectionId + '">' +
      '<div class="dh-sp-section-header">' + title + '</div>' +
      '<div class="dh-sp-grid">' + gridHtml + '</div>' +
    '</div>';
}

function _buildSidePanelToggleRow(id, label, checked, settingKey) {
  return '' +
    '<div class="dh-sp-setting-row" data-dh-sp-setting="' + settingKey + '">' +
      '<span class="dh-sp-setting-label">' + label + '</span>' +
      '<button type="button" class="dh-sp-toggle' + (checked ? ' dh-sp-toggle--on' : '') + '"' +
        ' id="' + id + '"' +
        ' role="switch" aria-checked="' + (checked ? 'true' : 'false') + '"' +
        ' aria-label="' + label + '">' +
        '<span class="dh-sp-toggle__track">' +
          '<span class="dh-sp-toggle__thumb"></span>' +
        '</span>' +
      '</button>' +
    '</div>';
}

function _buildSidePanelSettingsSection() {
  var prefs = _getDigitalHumanPrefs();
  return '' +
    '<div class="dh-sp-section" data-dh-sp-section="settings">' +
      '<div class="dh-sp-section-header">设置</div>' +
      '<div class="dh-sp-settings-list">' +
        _buildSidePanelToggleRow('dh-sp-toggle-auto-voice', '自动语音播报', prefs.auto_voice_enabled, 'auto_voice') +
        _buildSidePanelToggleRow('dh-sp-toggle-practice-score', '陪练评分播报', prefs.scenes.practice_score, 'practice_score') +
        _buildSidePanelToggleRow('dh-sp-toggle-practice-feedback', '逐轮点评播报', prefs.scenes.practice_turn_feedback, 'practice_turn_feedback') +
        _buildSidePanelToggleRow('dh-sp-toggle-assistant', '在岗助手播报', prefs.scenes.assistant, 'assistant') +
        _buildSidePanelToggleRow('dh-sp-toggle-knowledge', '知识问答播报', prefs.scenes.knowledge_qa, 'knowledge_qa') +
      '</div>' +
    '</div>';
}

function _getDigitalHumanPrefs() {
  if (typeof window.getDigitalHumanPreferences === 'function') {
    try {
      return window.getDigitalHumanPreferences();
    } catch (e) {}
  }
  try {
    var raw = localStorage.getItem('digital_human_prefs_v1');
    var obj = raw ? JSON.parse(raw) : {};
    var scenes = obj.scenes && typeof obj.scenes === 'object' ? obj.scenes : {};
    return {
      voice_muted: obj.voice_muted === true,
      auto_voice_enabled: obj.auto_voice_enabled !== false,
      scenes: {
        practice_score: scenes.practice_score !== false,
        practice_turn_feedback: scenes.practice_turn_feedback !== false,
        assistant: scenes.assistant !== false,
        knowledge_qa: scenes.knowledge_qa !== false,
      },
    };
  } catch (e) {
    return { voice_muted: false, auto_voice_enabled: true, scenes: { practice_score: true, practice_turn_feedback: true, assistant: true, knowledge_qa: true } };
  }
}

function _setSidePanelSetting(key, value) {
  var patch = {};
  if (key === 'auto_voice') {
    patch.auto_voice_enabled = value;
  } else {
    patch.scenes = {};
    patch.scenes[key] = value;
  }
  if (typeof window.setDigitalHumanPreferences === 'function') {
    try { window.setDigitalHumanPreferences(patch); } catch (e) {}
    _syncSidePanelSettings(_getDigitalHumanPrefs());
    return;
  }
  var prefs = _getDigitalHumanPrefs();
  var next = patch.scenes
    ? {
        auto_voice_enabled: prefs.auto_voice_enabled,
        scenes: Object.assign({}, prefs.scenes, patch.scenes),
      }
    : {
        auto_voice_enabled: patch.auto_voice_enabled !== undefined ? patch.auto_voice_enabled : prefs.auto_voice_enabled,
        scenes: prefs.scenes,
      };
  try {
    localStorage.setItem('digital_human_prefs_v1', JSON.stringify(next));
  } catch (e) {}
  _syncSidePanelSettings(next);
}

function _syncSidePanelSettings(prefs) {
  if (!prefs) prefs = _getDigitalHumanPrefs();
  var section = document.querySelector('[data-dh-sp-section="settings"]');
  if (!section) return;

  var toggles = [
    { id: 'dh-sp-toggle-auto-voice', key: 'auto_voice_enabled' },
    { id: 'dh-sp-toggle-practice-score', key: 'practice_score', isScene: true },
    { id: 'dh-sp-toggle-practice-feedback', key: 'practice_turn_feedback', isScene: true },
    { id: 'dh-sp-toggle-assistant', key: 'assistant', isScene: true },
    { id: 'dh-sp-toggle-knowledge', key: 'knowledge_qa', isScene: true },
  ];
  toggles.forEach(function (t) {
    var btn = document.getElementById(t.id);
    if (!btn) return;
    var checked = t.isScene ? prefs.scenes[t.key] : prefs[t.key];
    btn.setAttribute('aria-checked', checked ? 'true' : 'false');
    btn.classList.toggle('dh-sp-toggle--on', !!checked);
    var row = btn.closest('.dh-sp-setting-row');
    if (row) {
      row.classList.toggle('dh-sp-setting-row--disabled', !!t.isScene && !prefs.auto_voice_enabled);
    }
  });
}

function _getPageActionItems() {
  var page = _state.activePage || '';
  return _PAGE_ACTION_ITEMS[page] || [];
}

function _isSidePanelEnabled() {
  try {
    if (window.__DIGITAL_HUMAN_SIDEPANEL_ENABLED__ === true) return true;
    if (window.localStorage && window.localStorage.getItem('digital_human_sidepanel_enabled') === 'true') return true;
  } catch (error) {
    return false;
  }
  return false;
}

function _ensurePortalRoot() {
  var root = document.getElementById('dh-floating-root');
  var sidePanelEnabled = _isSidePanelEnabled();
  if (!root) {
    root = document.createElement('div');
    root.id = 'dh-floating-root';
    root.className = 'dh-floating-root';
    document.body.appendChild(root);
  }
  if (!root.querySelector('#dh-widget')) {
    root.innerHTML = _buildWidgetMarkup();
    if (sidePanelEnabled) {
      _renderSidePanel(root);
    } else {
      var toggle = root.querySelector('#dh-sidepanel-toggle');
      if (toggle) toggle.remove();
    }
  } else if (!sidePanelEnabled) {
    var existingToggle = root.querySelector('#dh-sidepanel-toggle');
    if (existingToggle) existingToggle.remove();
    var existingPanel = root.querySelector('#dh-sidepanel');
    if (existingPanel) existingPanel.remove();
  }
  return root;
}

function _initScene(container) {
  var rect = container.getBoundingClientRect();
  var width = Math.max(rect.width, 1);
  var height = Math.max(rect.height, 1);

  var renderer = new THREE.WebGLRenderer({
    alpha: true,
    antialias: true,
    preserveDrawingBuffer: true,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  container.appendChild(renderer.domElement);

  _state.renderer = renderer;
  _state.scene = new THREE.Scene();
  _state.camera = new THREE.PerspectiveCamera(29, width / height, 0.1, 100);
  _state.camera.position.set(0, 0.92, 4.35);
  _state.camera.lookAt(-0.08, 0.9, 0);

  _state.scene.add(new THREE.AmbientLight(0xffffff, 0.78));

  var keyLight = new THREE.DirectionalLight(0xffffff, 1.05);
  keyLight.position.set(2.2, 3.1, 2.8);
  _state.scene.add(keyLight);

  var fillLight = new THREE.DirectionalLight(0xdbeafe, 0.42);
  fillLight.position.set(-1.6, 1.2, 2.4);
  _state.scene.add(fillLight);

  _state.scene.add(new THREE.HemisphereLight(0xe0f2fe, 0x0f172a, 0.46));
  _state.clock = new THREE.Clock();

  _setupResizeObserver(container);
}

function _setupResizeObserver(container) {
  _disconnectResizeObserver();
  if (typeof ResizeObserver === 'undefined' || !container) return;

  _state.resizeObserver = new ResizeObserver(function (entries) {
    for (var i = 0; i < entries.length; i += 1) {
      var rect = entries[i].contentRect;
      _refreshSceneSize(rect.width, rect.height);
    }
  });
  _state.resizeObserver.observe(container);
}

function _disconnectResizeObserver() {
  if (_state.resizeObserver) {
    _state.resizeObserver.disconnect();
    _state.resizeObserver = null;
  }
}

function _refreshSceneSize(width, height) {
  var nextWidth = width;
  var nextHeight = height;

  if ((!nextWidth || !nextHeight) && _state.canvasWrap) {
    var rect = _state.canvasWrap.getBoundingClientRect();
    nextWidth = rect.width;
    nextHeight = rect.height;
  }

  nextWidth = Math.max(nextWidth || 0, 1);
  nextHeight = Math.max(nextHeight || 0, 1);

  if (_state.camera) {
    _state.camera.aspect = nextWidth / nextHeight;
    _state.camera.updateProjectionMatrix();
  }

  if (_state.renderer) {
    _state.renderer.setSize(nextWidth, nextHeight);
  }
}

function _loadModel(loadToken) {
  var loader = new GLTFLoader();
  loader.load(
    _state.modelUrl,
    function (gltf) {
      if (loadToken !== _state.loadToken || !_state.scene) return;

      var model = gltf.scene;
      var box = new THREE.Box3().setFromObject(model);
      var size = box.getSize(new THREE.Vector3());
      var center = box.getCenter(new THREE.Vector3());
      var maxDimension = Math.max(size.x, size.y, size.z);
      var scale = maxDimension > 0 ? 1.28 / maxDimension : 1;

      model.scale.setScalar(scale);
      model.position.set(-center.x * scale, -box.min.y * scale, -center.z * scale);
      model.rotation.y = -Math.PI / 2;
      model.updateMatrixWorld(true);

      _state.avatar = model;
      _state.scene.add(model);

      if (gltf.animations && gltf.animations.length > 1) {
        _state.mixer = new THREE.AnimationMixer(model);
        _state.animClips = gltf.animations;
        var agreeAction = _state.mixer.clipAction(gltf.animations[2]);
        agreeAction.play();
        _state.currentAnimIdx = 2;
      } else {
        var poseRig = _buildPoseRig(model);
        _state.poseGroups = poseRig.groups;
        _state.poseBases = poseRig.bases;
      }

      _state.loaded = true;
      _showError('');
      // Re-find bones now that avatar is loaded (mouse follow may have been enabled already)
      if (_state.mouseFollow && _state.mouseFollow.enabled) {
        _findFollowBones();
      }
      if (_state.pendingGreeting) {
        var pg = _state.pendingGreeting;
        _state.pendingGreeting = null;
        digitalHumanSpeak(pg.text, { bubbleKind: pg.bubbleKind, silent: !!pg.silent, trigger: pg.trigger || 'auto', scene: pg.scene || 'greeting' });
      }
      if (!_state.mixer) {
        digitalHumanSetPose('standby', true);
      }
    },
    function () {},
    function () {
      if (loadToken !== _state.loadToken) return;
      _showError('3D 模型加载失败，请检查 frontend/vendor/models/3D.glb');
    }
  );
}

function _buildPoseRig(modelRoot) {
  var parts = _classifyParts(modelRoot);
  var metrics = parts.metrics;

  var avatarRoot = new THREE.Group();
  avatarRoot.name = 'dh-avatar-root';
  avatarRoot.position.set(0, metrics.hipsY, 0);
  modelRoot.add(avatarRoot);

  var upperBodyGroup = new THREE.Group();
  upperBodyGroup.name = 'dh-upper-body';
  upperBodyGroup.position.set(0, metrics.torsoY - metrics.hipsY, 0);
  avatarRoot.add(upperBodyGroup);

  var headGroup = new THREE.Group();
  headGroup.name = 'dh-head';
  headGroup.position.set(0, metrics.neckY - metrics.hipsY, 0);
  avatarRoot.add(headGroup);

  var leftArmGroup = new THREE.Group();
  leftArmGroup.name = 'dh-left-arm';
  leftArmGroup.position.set(metrics.leftShoulderX, metrics.shoulderY - metrics.hipsY, metrics.leftShoulderZ);
  avatarRoot.add(leftArmGroup);

  var rightArmGroup = new THREE.Group();
  rightArmGroup.name = 'dh-right-arm';
  rightArmGroup.position.set(metrics.rightShoulderX, metrics.shoulderY - metrics.hipsY, metrics.rightShoulderZ);
  avatarRoot.add(rightArmGroup);

  var remainingGroup = new THREE.Group();
  remainingGroup.name = 'dh-lower-body';
  remainingGroup.position.set(0, 0, 0);
  avatarRoot.add(remainingGroup);

  _attachParts(parts.head, headGroup);
  _attachParts(parts.upperBody, upperBodyGroup);
  _attachParts(parts.leftArm, leftArmGroup);
  _attachParts(parts.rightArm, rightArmGroup);
  _attachParts(parts.lowerBody, remainingGroup);
  _attachParts(parts.unassigned, remainingGroup);

  return {
    groups: {
      avatarRoot: avatarRoot,
      upperBodyGroup: upperBodyGroup,
      headGroup: headGroup,
      leftArmGroup: leftArmGroup,
      rightArmGroup: rightArmGroup,
    },
    bases: _capturePoseBases({
      avatarRoot: avatarRoot,
      upperBodyGroup: upperBodyGroup,
      headGroup: headGroup,
      leftArmGroup: leftArmGroup,
      rightArmGroup: rightArmGroup,
    }),
  };
}

function _classifyParts(modelRoot) {
  var worldScale = modelRoot.getWorldScale(new THREE.Vector3());
  var uniformScale = worldScale.x || 1;
  var parts = [];

  modelRoot.traverse(function (child) {
    if (!child.isMesh || !child.geometry) return;
    if (!child.geometry.boundingBox) child.geometry.computeBoundingBox();
    var worldBox = child.geometry.boundingBox.clone().applyMatrix4(child.matrixWorld);
    var centerWorld = worldBox.getCenter(new THREE.Vector3());
    var sizeWorld = worldBox.getSize(new THREE.Vector3());
    var centerLocal = modelRoot.worldToLocal(centerWorld.clone());
    var sizeLocal = sizeWorld.clone().multiplyScalar(1 / uniformScale);

    parts.push({
      mesh: child,
      center: centerLocal,
      size: sizeLocal,
      minX: centerLocal.x - sizeLocal.x / 2,
      maxX: centerLocal.x + sizeLocal.x / 2,
      minY: centerLocal.y - sizeLocal.y / 2,
      maxY: centerLocal.y + sizeLocal.y / 2,
      minZ: centerLocal.z - sizeLocal.z / 2,
      maxZ: centerLocal.z + sizeLocal.z / 2,
    });
  });

  var bounds = _partsBounds(parts);
  var height = Math.max(bounds.maxY - bounds.minY, 0.001);
  var width = Math.max(bounds.maxX - bounds.minX, 0.001);

  var head = [];
  var upperBody = [];
  var lowerBody = [];
  var leftArm = [];
  var rightArm = [];
  var unassigned = [];

  for (var i = 0; i < parts.length; i += 1) {
    var part = parts[i];
    var relativeY = (part.center.y - bounds.minY) / height;
    var absX = Math.abs(part.center.x);

    if (relativeY >= 0.72) {
      head.push(part);
    } else if (relativeY >= 0.42 && relativeY <= 0.78 && absX >= width * 0.16) {
      if (part.center.x < 0) leftArm.push(part);
      else rightArm.push(part);
    } else if (relativeY >= 0.38) {
      upperBody.push(part);
    } else if (relativeY >= 0) {
      lowerBody.push(part);
    } else {
      unassigned.push(part);
    }
  }

  var leftShoulderX = leftArm.length ? _average(leftArm, 'center.x') * 0.8 : bounds.minX * 0.32;
  var rightShoulderX = rightArm.length ? _average(rightArm, 'center.x') * 0.8 : bounds.maxX * 0.32;
  var leftShoulderZ = leftArm.length ? _average(leftArm, 'center.z') * 0.5 : 0;
  var rightShoulderZ = rightArm.length ? _average(rightArm, 'center.z') * 0.5 : 0;

  return {
    head: head,
    upperBody: upperBody,
    lowerBody: lowerBody,
    leftArm: leftArm,
    rightArm: rightArm,
    unassigned: unassigned,
    metrics: {
      hipsY: bounds.minY + height * 0.33,
      torsoY: bounds.minY + height * 0.55,
      neckY: bounds.minY + height * 0.73,
      shoulderY: bounds.minY + height * 0.58,
      leftShoulderX: leftShoulderX,
      rightShoulderX: rightShoulderX,
      leftShoulderZ: leftShoulderZ,
      rightShoulderZ: rightShoulderZ,
    },
  };
}

function _partsBounds(parts) {
  var out = {
    minX: Infinity,
    maxX: -Infinity,
    minY: Infinity,
    maxY: -Infinity,
    minZ: Infinity,
    maxZ: -Infinity,
  };

  for (var i = 0; i < parts.length; i += 1) {
    out.minX = Math.min(out.minX, parts[i].minX);
    out.maxX = Math.max(out.maxX, parts[i].maxX);
    out.minY = Math.min(out.minY, parts[i].minY);
    out.maxY = Math.max(out.maxY, parts[i].maxY);
    out.minZ = Math.min(out.minZ, parts[i].minZ);
    out.maxZ = Math.max(out.maxZ, parts[i].maxZ);
  }

  if (!isFinite(out.minX)) {
    out.minX = out.minY = out.minZ = -0.5;
    out.maxX = out.maxY = out.maxZ = 0.5;
  }

  return out;
}

function _attachParts(parts, group) {
  for (var i = 0; i < parts.length; i += 1) {
    group.attach(parts[i].mesh);
  }
}

function _capturePoseBases(groups) {
  var bases = {};
  var keys = Object.keys(groups);
  for (var i = 0; i < keys.length; i += 1) {
    var key = keys[i];
    bases[key] = {
      position: groups[key].position.clone(),
      rotation: new THREE.Euler(
        groups[key].rotation.x,
        groups[key].rotation.y,
        groups[key].rotation.z
      ),
    };
  }
  return bases;
}

function _showError(message) {
  var errorEl = document.getElementById('dh-error');
  if (!errorEl) return;
  errorEl.textContent = String(message || '').trim();
  errorEl.classList.toggle('dh-error--visible', !!errorEl.textContent);
}

function _notifySpeechIssue(message) {
  var text = String(message || '').trim();
  if (!text) return;
  if (typeof window.showToast === 'function') {
    window.showToast(text, 'error');
    return;
  }
  _showError(text);
}

function _speechPriorityForOptions(options) {
  var opts = options && typeof options === 'object' ? options : {};
  var explicitPriority = Number(opts.priority);
  if (isFinite(explicitPriority)) return explicitPriority;
  if (opts.trigger === 'manual') return 4;
  if (String(opts.scene || '').trim() === 'greeting' || opts.bubbleKind === 'greeting') return 1;
  return 3;
}

function _isBubbleVisible() {
  var bubble = document.getElementById('dh-subtitle');
  return !!bubble && bubble.classList.contains('dh-speech-bubble--visible');
}

function _shouldInterruptForPriority(priority) {
  var activePriority = _state.speaking
    ? Number(_state.currentSpeechPriority || 0)
    : (_isBubbleVisible() ? Number(_state.visibleBubblePriority || 0) : 0);
  return !activePriority || priority >= activePriority;
}

function _beginSpeechRequest(priority) {
  _state.speechRequestToken += 1;
  _state.activeSpeechRequestToken = _state.speechRequestToken;
  _state.currentSpeechPriority = Number(priority || 0);
  return _state.activeSpeechRequestToken;
}

function _invalidateSpeechRequests() {
  _state.speechRequestToken += 1;
  _state.activeSpeechRequestToken = _state.speechRequestToken;
}

function _isActiveSpeechRequest(token) {
  return token === undefined || token === null || token === _state.activeSpeechRequestToken;
}

function _shouldUseBrowserTts() {
  if (typeof window.getDigitalHumanTtsProvider === 'function') {
    return window.getDigitalHumanTtsProvider() === 'browser';
  }
  return false;
}

function _hasPersistedAuthSession() {
  try {
    var token = String(localStorage.getItem('token') || '').trim();
    return !!token && token !== 'undefined' && token !== 'null';
  } catch (e) {
    return false;
  }
}

function _shouldGenerateVoice(options) {
  var opts = options && typeof options === 'object' ? options : {};
  if (_getDigitalHumanPrefs().voice_muted === true) return false;
  if (opts.silent) return false;
  if (opts.trigger === 'auto') {
    if (typeof window.shouldAutoPlayDigitalHumanVoice === 'function') {
      return !!window.shouldAutoPlayDigitalHumanVoice(String(opts.scene || '').trim());
    }
    return true;
  }
  return true;
}

function _speakWithBrowserTts(text, requestToken) {
  if (typeof window.SpeechSynthesisUtterance !== 'function' || !window.speechSynthesis || typeof window.speechSynthesis.speak !== 'function') {
    _handleSpeechFailure('当前浏览器不支持原生语音播报，已保留文字内容。', requestToken);
    return;
  }
  try {
    if (typeof window.speechSynthesis.cancel === 'function') window.speechSynthesis.cancel();
    var utterance = new window.SpeechSynthesisUtterance(text);
    utterance.lang = 'zh-CN';
    _state.browserUtterance = utterance;
    utterance.onend = function () {
      if (!_isActiveSpeechRequest(requestToken)) return;
      if (_state.browserUtterance !== utterance) return;
      _state.browserUtterance = null;
      _state.speaking = false;
      _state.currentSpeechPriority = 0;
      _setSpeechActionVisible(false);
      _scheduleBubbleHide(3500);
    };
    utterance.onerror = function () {
      if (!_isActiveSpeechRequest(requestToken)) return;
      if (_state.browserUtterance !== utterance) return;
      _state.browserUtterance = null;
      _handleSpeechFailure('浏览器语音播报失败，已保留文字内容。', requestToken);
    };
    window.speechSynthesis.speak(utterance);
  } catch (e) {
    _state.browserUtterance = null;
    _handleSpeechFailure('浏览器语音播报失败，已保留文字内容。', requestToken);
  }
}

function _setSpeechActionVisible(visible) {
  var bubble = document.getElementById('dh-subtitle');
  var btn = document.getElementById('dh-stop-speech-btn');
  if (bubble) bubble.classList.toggle('dh-speech-bubble--speaking', !!visible);
  if (!btn) return;
  btn.hidden = !visible;
  btn.setAttribute('aria-hidden', visible ? 'false' : 'true');
}

function _setBubble(text, kind, priority) {
  var bubble = document.getElementById('dh-subtitle');
  var label = document.getElementById('dh-subtitle-label');
  var textEl = document.getElementById('dh-subtitle-text');
  if (!bubble || !label || !textEl) return;

  _state.bubbleKind = kind || 'score';
  _state.visibleBubblePriority = Number(priority || 0);
  bubble.dataset.kind = _state.bubbleKind;
  var labelText = _bubbleLabelForKind(_state.bubbleKind);
  label.textContent = labelText;
  label.style.display = labelText ? '' : 'none';
  textEl.textContent = text;
  textEl.scrollTop = 0;
  bubble.classList.add('dh-speech-bubble--visible');
}

function _clearBubble() {
  var bubble = document.getElementById('dh-subtitle');
  var textEl = document.getElementById('dh-subtitle-text');
  if (textEl) textEl.textContent = '';
  _state.visibleBubblePriority = 0;
  _setSpeechActionVisible(false);
  if (bubble) bubble.classList.remove('dh-speech-bubble--visible');
}

function _bubbleLabelForKind(kind) {
  if (kind === 'greeting') return '';
  if (kind === 'emotion') return '';
  if (kind === 'score') return '评分点评';
  if (kind === 'turn_feedback') return '逐轮点评';
  if (kind === 'exam_coach') return '陪考教练';
  if (kind === 'exam_pressure') return '考试提醒';
  if (kind === 'exam_debrief') return '考后复盘';
  if (kind === 'assistant' || kind === 'on_duty_assistant') return '在岗助手';
  if (kind === 'qa' || kind === 'qa_feedback' || kind === 'knowledge_qa') return '知识问答';
  if (kind === 'quick_query') return '数据查询';
  if (kind === 'daily' || kind === 'home') return '今日播报';
  return '数字陪练';
}

function _supportsMediaSourceMp3() {
  try {
    return typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported('audio/mpeg');
  } catch (e) {
    return false;
  }
}

function _playAudioStream(url, payload, requestToken) {
  var abortCtrl = new AbortController();
  _state.streamAbort = abortCtrl;

  var mediaSource = new MediaSource();
  _state.mediaSource = mediaSource;

  var audio = new Audio();
  _state.audioEl = audio;
  audio.src = URL.createObjectURL(mediaSource);

  var sourceBuffer = null;
  var playing = false;

  mediaSource.addEventListener('sourceopen', function () {
    sourceBuffer = mediaSource.addSourceBuffer('audio/mpeg');

    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: abortCtrl.signal,
    }).then(function (response) {
      if (!response.ok) {
        throw new Error('stream_tts_http_' + response.status);
      }
      _state.streamReader = response.body.getReader();
      return _pumpStream(_state.streamReader, sourceBuffer, mediaSource, audio, requestToken, function () {
        if (!playing) {
          playing = true;
          audio.play().catch(function () {});
        }
      });
    }).then(function () {
      if (!_isActiveSpeechRequest(requestToken)) return;
      if (mediaSource.readyState === 'open') {
        mediaSource.endOfStream();
      }
      if (!playing) {
        playing = true;
        audio.play().catch(function () {});
      }
    }).catch(function (err) {
      if (abortCtrl.signal.aborted) return;
      if (!_isActiveSpeechRequest(requestToken)) return;
      _handleSpeechFailure('语音合成失败，已保留文字内容。', requestToken);
    });
  }, { once: true });

  audio.onended = function () {
    _handleSpeechEnded(requestToken);
  };
  audio.onerror = function () {
    _handleSpeechFailure('语音播放失败，已保留文字内容。', requestToken);
  };
}

function _pumpStream(reader, sourceBuffer, mediaSource, audio, requestToken, onFirstChunk) {
  var firstChunkSeen = false;

  function appendNext() {
    return reader.read().then(function (result) {
      if (result.done) return;
      if (!_isActiveSpeechRequest(requestToken)) return;

      if (!firstChunkSeen) {
        firstChunkSeen = true;
        onFirstChunk();
      }

      return new Promise(function (resolve) {
        sourceBuffer.addEventListener('updateend', function onupdate() {
          sourceBuffer.removeEventListener('updateend', onupdate);
          resolve(appendNext());
        }, { once: false });
        sourceBuffer.appendBuffer(result.value);
      });
    });
  }

  return appendNext();
}

function _playAudioLegacy(payload, requestToken) {
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/tts/synthesize', true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.responseType = 'blob';
  xhr.onload = function () {
    if (!_isActiveSpeechRequest(requestToken)) return;
    if (xhr.status === 200 && xhr.response) {
      _playAudio(xhr.response, requestToken);
      return;
    }
    _handleSpeechFailure('语音合成失败，已保留文字内容。', requestToken);
  };
  xhr.onerror = function () {
    _handleSpeechFailure('语音合成失败，已保留文字内容。', requestToken);
  };
  xhr.send(JSON.stringify(payload));
}

function _playAudio(blob, requestToken) {
  if (!_isActiveSpeechRequest(requestToken)) return;
  _releaseAudioUrl();
  _state.audioUrl = URL.createObjectURL(blob);

  var audio = new Audio(_state.audioUrl);
  _state.audioEl = audio;
  audio.onended = function () {
    _handleSpeechEnded(requestToken);
  };
  audio.onerror = function () {
    _handleSpeechFailure('语音播放失败，已保留文字内容。', requestToken);
  };
  audio.play().catch(function () {
    _handleSpeechFailure('语音播放失败，已保留文字内容。', requestToken);
  });
}

function _handleSpeechEnded(requestToken) {
  if (!_isActiveSpeechRequest(requestToken)) return;
  _state.speaking = false;
  _state.currentSpeechPriority = 0;
  _state.browserUtterance = null;
  _stopSpeakingMicro();
  _state.audioEl = null;
  _releaseAudioUrl();
  _setSpeechActionVisible(false);
  _scheduleBubbleHide(320);
  if (_state.pendingEmotion) {
    var emotion = _state.pendingEmotion;
    _state.pendingEmotion = null;
    setTimeout(function () {
      if (!_state.speaking) _playEmotion(emotion);
    }, 600);
  }
}

function _handleSpeechFailure(message, requestToken) {
  if (!_isActiveSpeechRequest(requestToken)) return;
  _state.speaking = false;
  _state.currentSpeechPriority = 0;
  _state.browserUtterance = null;
  _stopSpeakingMicro();
  _state.audioEl = null;
  _releaseAudioUrl();
  _setSpeechActionVisible(false);
  if (_state.bubbleKind !== 'greeting') {
    _notifySpeechIssue(message);
  }
  _scheduleBubbleHide(3500);
}

function _scheduleBubbleHide(delay) {
  _clearSpeechTimeout();
  _state.speechTimeoutId = setTimeout(function () {
    _clearBubble();
    _state.speechTimeoutId = null;
  }, delay);
}

function _clearSpeechTimeout() {
  if (_state.speechTimeoutId) {
    clearTimeout(_state.speechTimeoutId);
    _state.speechTimeoutId = null;
  }
}

function _releaseAudioUrl() {
  if (_state.audioUrl) {
    URL.revokeObjectURL(_state.audioUrl);
    _state.audioUrl = '';
  }
}

function _startRenderLoop() {
  if (_state.animFrameId) return;

  function frame() {
    _state.animFrameId = requestAnimationFrame(frame);
    if (!_state.visible) return;

    var delta = _state.clock ? _state.clock.getDelta() : 0;
    if (_state.mixer && delta > 0) {
      _state.mixer.update(delta);
    }
    // Only apply pose rig if no built-in animations
    if (!_state.mixer) {
      _applyCurrentPose(false, delta);
    }

    // Speaking micro-interactions (additive, before mouse follow)
    _applySpeakingMicro(delta);

    // Mouth open/close during speaking
    var speakElapsed = _state.speakingMicro.active ? (performance.now() - _state.speakingMicro.startTime) / 1000 : 0;
    _applyMouthAnimation(speakElapsed);

    // Apply mouse follow (additive rotation after pose)
    var mfAlpha = Math.min(1, Math.max(0.12, (delta || 0.016) * 7.5));
    _applyMouseFollow(mfAlpha);

    if (_state.renderer && _state.scene && _state.camera) {
      _state.renderer.render(_state.scene, _state.camera);
    }
  }

  frame();
}

function _applyCurrentPose(immediate, delta) {
  if (!_state.poseGroups || !_state.poseBases) return;
  var pose = _POSE_PRESETS[_state.targetPoseName] || _POSE_PRESETS.standby;
  var keys = Object.keys(_state.poseGroups);
  var alpha = immediate ? 1 : Math.min(1, Math.max(0.12, (delta || 0.016) * 7.5));

  for (var i = 0; i < keys.length; i += 1) {
    var key = keys[i];
    var group = _state.poseGroups[key];
    var base = _state.poseBases[key];
    var target = pose[key] || {};
    var targetPos = target.position || [0, 0, 0];
    var targetRot = target.rotation || [0, 0, 0];

    _dampVector3(
      group.position,
      base.position.x + targetPos[0],
      base.position.y + targetPos[1],
      base.position.z + targetPos[2],
      alpha
    );
    group.rotation.x = _dampNumber(group.rotation.x, base.rotation.x + targetRot[0], alpha);
    group.rotation.y = _dampNumber(group.rotation.y, base.rotation.y + targetRot[1], alpha);
    group.rotation.z = _dampNumber(group.rotation.z, base.rotation.z + targetRot[2], alpha);
  }

  if (immediate) {
    _state.currentPoseName = _state.targetPoseName;
    _syncPoseDataset();
    return;
  }

  if (_poseCloseEnough()) {
    _state.currentPoseName = _state.targetPoseName;
    _syncPoseDataset();
  }
}

function _poseCloseEnough() {
  if (!_state.poseGroups || !_state.poseBases) return true;
  var pose = _POSE_PRESETS[_state.targetPoseName] || _POSE_PRESETS.standby;
  var keys = Object.keys(_state.poseGroups);

  for (var i = 0; i < keys.length; i += 1) {
    var key = keys[i];
    var group = _state.poseGroups[key];
    var base = _state.poseBases[key];
    var target = pose[key] || {};
    var targetPos = target.position || [0, 0, 0];
    var targetRot = target.rotation || [0, 0, 0];

    if (Math.abs(group.position.x - (base.position.x + targetPos[0])) > 0.003) return false;
    if (Math.abs(group.position.y - (base.position.y + targetPos[1])) > 0.003) return false;
    if (Math.abs(group.position.z - (base.position.z + targetPos[2])) > 0.003) return false;
    if (Math.abs(group.rotation.x - (base.rotation.x + targetRot[0])) > 0.01) return false;
    if (Math.abs(group.rotation.y - (base.rotation.y + targetRot[1])) > 0.01) return false;
    if (Math.abs(group.rotation.z - (base.rotation.z + targetRot[2])) > 0.01) return false;
  }

  return true;
}

function _syncPoseDataset() {
  var widget = document.getElementById('dh-widget');
  if (widget) widget.dataset.pose = _state.targetPoseName || 'standby';
  _updateSidePanelActivePose();
}

function _dampVector3(vector, x, y, z, alpha) {
  vector.x = _dampNumber(vector.x, x, alpha);
  vector.y = _dampNumber(vector.y, y, alpha);
  vector.z = _dampNumber(vector.z, z, alpha);
}

function _dampNumber(current, target, alpha) {
  return current + (target - current) * alpha;
}

function _clamp(val, min, max) {
  return val < min ? min : (val > max ? max : val);
}

function _enableMouseFollow() {
  var mf = _state.mouseFollow;
  if (mf.enabled) return;
  mf.enabled = true;
  mf.targetHeadYaw = 0;
  mf.targetHeadPitch = 0;
  mf.targetBodyYaw = 0;
  mf.headYaw = 0;
  mf.headPitch = 0;
  mf.bodyYaw = 0;
  // Try to find bones for GLB models with skeleton
  _findFollowBones();
  mf.onMove = function (e) {
    if (!mf.enabled) return;
    var canvas = _state.renderer ? _state.renderer.domElement : null;
    if (!canvas) return;
    var rect = canvas.getBoundingClientRect();
    // Head is roughly at the upper quarter of the canvas
    var cx = rect.left + rect.width / 2;
    var cy = rect.top + rect.height * 0.22;
    var dx = e.clientX - cx;
    var dy = e.clientY - cy;
    // Normalize direction, clamp rotation range
    mf.targetHeadYaw = _clamp((dx / (window.innerWidth * 0.3)) * 0.5, -0.6, 0.6);
    mf.targetHeadPitch = _clamp((-dy / (window.innerHeight * 0.3)) * 0.3, -0.4, 0.4);
    mf.targetBodyYaw = mf.targetHeadYaw * 0.4;
  };
  document.addEventListener('mousemove', mf.onMove);
}

function _disableMouseFollow() {
  var mf = _state.mouseFollow;
  if (!mf.enabled) return;
  mf.enabled = false;
  if (mf.onMove) {
    document.removeEventListener('mousemove', mf.onMove);
    mf.onMove = null;
  }
  mf.headYaw = 0;
  mf.headPitch = 0;
  mf.bodyYaw = 0;
  mf.targetHeadYaw = 0;
  mf.targetHeadPitch = 0;
  mf.targetBodyYaw = 0;
  mf.bonesFound = false;
  mf.headBone = null;
  mf.neckBone = null;
  mf.spineBone = null;
  mf.jawBone = null;
}

function _findFollowBones() {
  var mf = _state.mouseFollow;
  if (!_state.avatar) return;
  if (_state.mixer) {
    // GLB model with built-in animations — search for skeleton bones
    _state.avatar.traverse(function (obj) {
      if (!obj.isBone) return;
      var name = (obj.name || '').toLowerCase();
      if ((name.indexOf('head') >= 0 || name.indexOf('头部') >= 0) && !mf.headBone) {
        mf.headBone = obj;
      } else if (name.indexOf('neck') >= 0 && !mf.neckBone) {
        mf.neckBone = obj;
      } else if ((name.indexOf('spine') >= 0 || name.indexOf('chest') >= 0 || name.indexOf('torso') >= 0 || name.indexOf('upper_body') >= 0) && !mf.spineBone) {
        mf.spineBone = obj;
      }
      if ((name.indexOf('jaw') >= 0 || name.indexOf('chin') >= 0 || name.indexOf('下颌') >= 0 || name.indexOf('下巴') >= 0) && !mf.jawBone) {
        mf.jawBone = obj;
      }
    });
    mf.bonesFound = !!(mf.headBone || mf.neckBone || mf.spineBone);
  }
  // Search for mouth-related morph targets
  _findMouthMorphs();
}

function _findMouthMorphs() {
  if (!_state.avatar) return;
  _state.mouthMorphs = [];
  _state.avatar.traverse(function (obj) {
    if (!obj.isMesh || !obj.morphTargetDictionary) return;
    var dict = obj.morphTargetDictionary;
    var names = Object.keys(dict);
    for (var i = 0; i < names.length; i++) {
      var lk = names[i].toLowerCase();
      if (lk.indexOf('mouth') >= 0 || lk.indexOf('jaw') >= 0 || lk.indexOf('open') >= 0 || lk.indexOf('viseme') >= 0 || lk.indexOf('嘴') >= 0) {
        _state.mouthMorphs.push({ mesh: obj, index: dict[names[i]], name: names[i] });
      }
    }
  });
}

function _applyMouthAnimation(elapsed) {
  if (!_state.speakingMicro.active) {
    _resetMouth();
    return;
  }
  // Two sine waves at different frequencies for irregular mouth movement
  var a = Math.abs(Math.sin(elapsed * 5.5)) * 0.65 + Math.abs(Math.sin(elapsed * 8.3)) * 0.35;
  // Occasional brief pause (~0.25s every 3-4s) to mimic natural speech rhythm
  var cycle = elapsed % (3.2 + Math.sin(elapsed * 0.37) * 0.8);
  if (cycle < 0.25) a *= cycle / 0.25;
  var openAmount = Math.min(1, Math.max(0, a));

  var mf = _state.mouseFollow;
  if (mf.jawBone) {
    mf.jawBone.rotation.x = openAmount * 0.28;
  }
  if (_state.mouthMorphs) {
    for (var i = 0; i < _state.mouthMorphs.length; i++) {
      var m = _state.mouthMorphs[i];
      if (m.mesh && m.mesh.morphTargetInfluences) {
        m.mesh.morphTargetInfluences[m.index] = openAmount;
      }
    }
  }
}

function _resetMouth() {
  var mf = _state.mouseFollow;
  if (mf.jawBone) {
    mf.jawBone.rotation.x *= 0.8;
    if (Math.abs(mf.jawBone.rotation.x) < 0.005) mf.jawBone.rotation.x = 0;
  }
  if (_state.mouthMorphs) {
    for (var i = 0; i < _state.mouthMorphs.length; i++) {
      var m = _state.mouthMorphs[i];
      if (m.mesh && m.mesh.morphTargetInfluences) {
        m.mesh.morphTargetInfluences[m.index] *= 0.8;
        if (m.mesh.morphTargetInfluences[m.index] < 0.005) m.mesh.morphTargetInfluences[m.index] = 0;
      }
    }
  }
}

function _applyMouseFollow(alpha) {
  var mf = _state.mouseFollow;
  if (!mf.enabled) return;
  if (_state.dragRotate.active) return;
  var dampAlpha = Math.min(1, alpha * 0.5);
  mf.headYaw = _dampNumber(mf.headYaw, mf.targetHeadYaw, dampAlpha);
  mf.headPitch = _dampNumber(mf.headPitch, mf.targetHeadPitch, dampAlpha);
  mf.bodyYaw = _dampNumber(mf.bodyYaw, mf.targetBodyYaw, dampAlpha);

  // Path 1: pose rig groups (non-animated models)
  if (_state.poseGroups && !_state.mixer) {
    var head = _state.poseGroups.headGroup;
    var upper = _state.poseGroups.upperBodyGroup;
    if (head) {
      head.rotation.y += mf.headYaw;
      head.rotation.x += mf.headPitch;
    }
    if (upper) {
      upper.rotation.y += mf.bodyYaw;
    }
    return;
  }

  // Path 2: bones for GLB models with skeleton
  if (mf.bonesFound && _state.mixer) {
    if (mf.headBone) {
      mf.headBone.rotation.y += mf.headYaw;
      mf.headBone.rotation.x += mf.headPitch;
    }
    if (mf.neckBone) {
      mf.neckBone.rotation.y += mf.headYaw * 0.5;
      mf.neckBone.rotation.x += mf.headPitch * 0.5;
    }
    if (mf.spineBone) {
      mf.spineBone.rotation.y += mf.bodyYaw;
    }
    return;
  }

  // Path 3: fallback — apply rotation to the whole avatar root
  if (_state.avatar && _state.mixer) {
    _state.avatar.rotation.y += mf.bodyYaw * 0.5;
  }
}

// =============== Login page interactions ===============

var _loginInteractionBound = false;
var _loginInteractionState = {
  onFocusUser: null,
  onFocusPass: null,
  onBlur: null,
  onInput: null,
  onBtnEnter: null,
  onBtnLeave: null,
  inputTimeout: null,
};

function _bindLoginInteractions() {
  if (_loginInteractionBound || _state.activePage !== 'login') return;
  _loginInteractionBound = true;
  var userField = document.getElementById('login-user');
  var passField = document.getElementById('login-pass');
  var loginBtn = document.getElementById('login-btn');
  if (!userField || !passField) return;

  _loginInteractionState.onFocusUser = function () {
    if (_state.activePage !== 'login') return;
    _state.mouseFollow.targetHeadYaw = -0.25;
    digitalHumanSpeak('请输入账号', { bubbleKind: 'greeting', silent: true, trigger: 'auto', scene: 'login' });
  };
  _loginInteractionState.onFocusPass = function () {
    if (_state.activePage !== 'login') return;
    _state.mouseFollow.targetHeadYaw = -0.25;
    digitalHumanMicroExpression('slight_nod');
    digitalHumanSpeak('请输入密码', { bubbleKind: 'greeting', silent: true, trigger: 'auto', scene: 'login' });
  };
  _loginInteractionState.onBlur = function () {
    if (_state.activePage !== 'login') return;
    _state.mouseFollow.targetHeadYaw = 0;
    _state.mouseFollow.targetHeadPitch = 0;
  };
  _loginInteractionState.onInput = function () {
    if (_state.activePage !== 'login') return;
    clearTimeout(_loginInteractionState.inputTimeout);
    _loginInteractionState.inputTimeout = setTimeout(function () {
      digitalHumanMicroExpression('slight_nod');
    }, 600);
  };
  _loginInteractionState.onBtnEnter = function () {
    if (_state.activePage !== 'login') return;
    digitalHumanSetPose('encourage');
    digitalHumanSpeak('准备好了吗？', { bubbleKind: 'greeting', silent: true, trigger: 'auto', scene: 'login' });
  };
  _loginInteractionState.onBtnLeave = function () {
    if (_state.activePage !== 'login') return;
    digitalHumanSetPose('standby');
  };

  userField.addEventListener('focus', _loginInteractionState.onFocusUser);
  passField.addEventListener('focus', _loginInteractionState.onFocusPass);
  userField.addEventListener('blur', _loginInteractionState.onBlur);
  passField.addEventListener('blur', _loginInteractionState.onBlur);
  userField.addEventListener('input', _loginInteractionState.onInput);
  passField.addEventListener('input', _loginInteractionState.onInput);
  if (loginBtn) {
    loginBtn.addEventListener('mouseenter', _loginInteractionState.onBtnEnter);
    loginBtn.addEventListener('mouseleave', _loginInteractionState.onBtnLeave);
  }
}

function _unbindLoginInteractions() {
  if (!_loginInteractionBound) return;
  _loginInteractionBound = false;
  var userField = document.getElementById('login-user');
  var passField = document.getElementById('login-pass');
  var loginBtn = document.getElementById('login-btn');

  if (userField) {
    userField.removeEventListener('focus', _loginInteractionState.onFocusUser);
    userField.removeEventListener('blur', _loginInteractionState.onBlur);
    userField.removeEventListener('input', _loginInteractionState.onInput);
  }
  if (passField) {
    passField.removeEventListener('focus', _loginInteractionState.onFocusPass);
    passField.removeEventListener('blur', _loginInteractionState.onBlur);
    passField.removeEventListener('input', _loginInteractionState.onInput);
  }
  if (loginBtn) {
    loginBtn.removeEventListener('mouseenter', _loginInteractionState.onBtnEnter);
    loginBtn.removeEventListener('mouseleave', _loginInteractionState.onBtnLeave);
  }
  clearTimeout(_loginInteractionState.inputTimeout);
}

// Login success / failure animations (called from app.js login())
function digitalHumanLoginSuccess() {
  digitalHumanSetPose('celebrate');
  var _name = localStorage.getItem('displayName') || '';
  digitalHumanSpeak('欢迎回来' + (_name ? '，' + _name : '') + '！', { bubbleKind: 'greeting', silent: false, trigger: 'auto', scene: 'login' });
  _unbindLoginInteractions();
}

function digitalHumanLoginFail() {
  digitalHumanSetPose('encourage');
  digitalHumanSpeak('没关系，再试一次', { bubbleKind: 'greeting', silent: true, trigger: 'auto', scene: 'login' });
}

function _forwardClick(event) {
  var wrap = document.getElementById('dh-canvas-wrap');
  if (!wrap) return;
  wrap.style.pointerEvents = 'none';
  var el = document.elementFromPoint(event.clientX, event.clientY);
  wrap.style.pointerEvents = 'auto';
  if (!el || el === wrap || wrap.contains(el)) return;
  el.dispatchEvent(new MouseEvent('click', {
    bubbles: true,
    cancelable: true,
    clientX: event.clientX,
    clientY: event.clientY
  }));
}

function _bindEvents() {
  if (!_state.listenersBound) {
    _state.onDocumentClick = function (event) {
      _state.lastActivityTime = Date.now();
      var widget = document.getElementById('dh-widget');
      var panel = document.getElementById('dh-sidepanel');
      if (widget && widget.contains(event.target)) return;
      if (panel && panel.contains(event.target)) return;
      _closeQuickMenu();
      _closeSidePanel();
    };

    _state.onDocumentKeydown = function (event) {
      _state.lastActivityTime = Date.now();
      if (event.key === 'Escape') {
        _closeQuickMenu();
        _closeSidePanel();
      }
    };

    _state.onVisibilityChange = function () {
      _state.visible = !document.hidden;
    };

    _state.onActivity = function () {
      _state.lastActivityTime = Date.now();
    };

    document.addEventListener('click', _state.onDocumentClick);
    document.addEventListener('keydown', _state.onDocumentKeydown);
    document.addEventListener('visibilitychange', _state.onVisibilityChange);
    document.addEventListener('touchstart', _state.onActivity);
    document.addEventListener('mousemove', _state.onActivity);
    _state.listenersBound = true;
    _startIdleCheck();
  }

  var wrap = document.getElementById('dh-canvas-wrap');
  if (!wrap) return;

  // --- Drag-to-rotate handlers ---
  _state.onDragStart = function (e) {
    if (!_state.avatar) return;
    if (!_isPreferenceEnabled('drag_rotate')) return;
    var cx, cy;
    if (e.type === 'touchstart') {
      if (!e.touches || !e.touches.length) return;
      cx = e.touches[0].clientX;
      cy = e.touches[0].clientY;
    } else {
      cx = e.clientX;
      cy = e.clientY;
    }
    var hitEvent = { clientX: cx, clientY: cy };
    if (!_hitAvatar(hitEvent)) return;

    var dr = _state.dragRotate;
    dr.active = true;
    dr.moved = false;
    dr.startX = cx;
    dr.startY = cy;
    dr.startRotY = _state.avatar.rotation.y;
    dr.startRotX = _state.avatar.rotation.x;
    if (e.type === 'touchstart') e.preventDefault();
  };

  _state.onDragMove = function (e) {
    var dr = _state.dragRotate;
    if (!dr.active) return;
    var cx, cy;
    if (e.type === 'touchmove') {
      if (!e.touches || !e.touches.length) return;
      cx = e.touches[0].clientX;
      cy = e.touches[0].clientY;
    } else {
      cx = e.clientX;
      cy = e.clientY;
    }
    var dx = cx - dr.startX;
    var dy = cy - dr.startY;
    var threshold = 5;
    if (!dr.moved && (Math.abs(dx) > threshold || Math.abs(dy) > threshold)) {
      dr.moved = true;
    }
    if (!dr.moved) return;
    var sensitivity = 0.008;
    _state.avatar.rotation.y = dr.startRotY + dx * sensitivity;
    _state.avatar.rotation.x = _clamp(dr.startRotX + dy * sensitivity, -0.5, 0.5);
    if (e.type === 'touchmove') e.preventDefault();
  };

  _state.onDragEnd = function () {
    _state.dragRotate.active = false;
  };

  wrap.addEventListener('mousedown', _state.onDragStart);
  document.addEventListener('mousemove', _state.onDragMove);
  document.addEventListener('mouseup', _state.onDragEnd);
  wrap.addEventListener('touchstart', _state.onDragStart, { passive: false });
  document.addEventListener('touchmove', _state.onDragMove, { passive: false });
  document.addEventListener('touchend', _state.onDragEnd);

  // --- Canvas click (only fires if not dragged) ---
  _state.onCanvasClick = function (event) {
    event.stopPropagation();
    if (_state.dragRotate.moved) {
      _state.dragRotate.moved = false;
      return;
    }
    if (_hitAvatar(event)) {
      _toggleQuickMenu();
      return;
    }
    _closeQuickMenu();
    _forwardClick(event);
  };

  wrap.onclick = _state.onCanvasClick;

  document.querySelectorAll('.dh-quick-menu-item').forEach(function (item) {
    item.onclick = function (event) {
      event.stopPropagation();
      _handleQuickAction(item.getAttribute('data-dh-action'));
    };
  });

  var stopBtn = document.getElementById('dh-stop-speech-btn');
  if (stopBtn) {
    stopBtn.onclick = function (event) {
      event.stopPropagation();
      digitalHumanStopSpeech({ preserveBubble: true });
    };
  }

  var toggleBtn = document.getElementById('dh-sidepanel-toggle');
  if (toggleBtn) {
    toggleBtn.onclick = function (event) {
      event.stopPropagation();
      _toggleSidePanel();
    };
  }

  _state.canvasWrap = wrap;
}

function _unbindEvents() {
  if (_state.listenersBound) {
    document.removeEventListener('click', _state.onDocumentClick);
    document.removeEventListener('keydown', _state.onDocumentKeydown);
    document.removeEventListener('visibilitychange', _state.onVisibilityChange);
    document.removeEventListener('touchstart', _state.onActivity);
    document.removeEventListener('mousemove', _state.onActivity);
  }

  _stopIdleCheck();
  _clearEmotionTimeout();
  _clearMicroExpressionTimeout();
  _stopSpeakingMicro();

  // Clean up drag-rotate listeners
  if (_state.canvasWrap) {
    _state.canvasWrap.removeEventListener('mousedown', _state.onDragStart);
    _state.canvasWrap.removeEventListener('touchstart', _state.onDragStart);
  }
  document.removeEventListener('mousemove', _state.onDragMove);
  document.removeEventListener('mouseup', _state.onDragEnd);
  document.removeEventListener('touchmove', _state.onDragMove);
  document.removeEventListener('touchend', _state.onDragEnd);
  _state.dragRotate.active = false;
  _state.dragRotate.moved = false;
  _state.onDragStart = null;
  _state.onDragMove = null;
  _state.onDragEnd = null;

  _state.listenersBound = false;
  _state.onDocumentClick = null;
  _state.onDocumentKeydown = null;
  _state.onVisibilityChange = null;
  _state.onActivity = null;
  _state.onMenuButtonClick = null;
}

function _toggleQuickMenu() {
  if (_state.menuOpen) _closeQuickMenu();
  else _openQuickMenu();
}

function _openQuickMenu() {
  var widget = document.getElementById('dh-widget');
  var menu = document.getElementById('dh-quick-menu');
  if (!widget || !menu) return;
  _closeSidePanel();
  _state.menuOpen = true;
  widget.classList.add('dh-menu-open');
  menu.setAttribute('aria-hidden', 'false');
}

function _closeQuickMenu() {
  var widget = document.getElementById('dh-widget');
  var menu = document.getElementById('dh-quick-menu');
  _state.menuOpen = false;
  if (widget) widget.classList.remove('dh-menu-open');
  if (menu) menu.setAttribute('aria-hidden', 'true');
}

/* ====== Side Panel ====== */

function _renderSidePanel(root) {
  if (!_isSidePanelEnabled()) return;
  var existing = document.getElementById('dh-sidepanel');
  if (existing) existing.remove();
  var panelHtml = _buildSidePanelMarkup();
  var tmp = document.createElement('div');
  tmp.innerHTML = panelHtml;
  var panel = tmp.firstElementChild;
  root.appendChild(panel);

  // Bind close button
  var closeBtn = document.getElementById('dh-sidepanel-close');
  if (closeBtn) {
    closeBtn.onclick = function (ev) {
      ev.stopPropagation();
      _closeSidePanel();
    };
  }

  // Bind all action buttons
  panel.querySelectorAll('.dh-sp-btn').forEach(function (btn) {
    btn.onclick = function (ev) {
      ev.stopPropagation();
      var rawAction = btn.getAttribute('data-dh-sp-action') || '';
      _sidePanelFlashFeedback(btn);
      _handleSidePanelAction(rawAction);
    };
  });

  // Bind settings toggles
  panel.querySelectorAll('.dh-sp-toggle').forEach(function (toggle) {
    toggle.onclick = function (ev) {
      ev.stopPropagation();
      var row = toggle.closest('.dh-sp-setting-row');
      if (!row) return;
      if (row.classList.contains('dh-sp-setting-row--disabled')) return;
      var settingKey = row.getAttribute('data-dh-sp-setting') || '';
      var isChecked = toggle.getAttribute('aria-checked') === 'true';
      _setSidePanelSetting(settingKey, !isChecked);
    };
  });

  _updateSidePanelActivePose();
  _syncSidePanelSettings();
}

function _rebuildSidePanelPageActions() {
  var panel = document.getElementById('dh-sidepanel');
  if (!panel) return;
  var section = panel.querySelector('[data-dh-sp-section="page-actions"]');
  var body = document.getElementById('dh-sidepanel-body');
  if (!body) return;

  var pageItems = _getPageActionItems();
  if (section) section.remove();
  if (pageItems.length === 0) return;

  var newSectionHtml = _buildSidePanelSection('page-actions', '页面操作', pageItems);
  var generalSection = body.querySelector('[data-dh-sp-section="general"]');
  if (generalSection) {
    var tmp = document.createElement('div');
    tmp.innerHTML = newSectionHtml;
    var newSection = tmp.firstElementChild;
    newSection.querySelectorAll('.dh-sp-btn').forEach(function (btn) {
      btn.onclick = function (ev) {
        ev.stopPropagation();
        var rawAction = btn.getAttribute('data-dh-sp-action') || '';
        _sidePanelFlashFeedback(btn);
        _handleSidePanelAction(rawAction);
      };
    });
    body.insertBefore(newSection, generalSection);
  }
}

function _updateSidePanelActivePose() {
  var panel = document.getElementById('dh-sidepanel');
  if (!panel) return;
  panel.querySelectorAll('.dh-sp-btn[data-dh-sp-action^="setPose:"]').forEach(function (btn) {
    var action = btn.getAttribute('data-dh-sp-action') || '';
    var poseName = action.replace('setPose:', '');
    if (poseName === (_state.targetPoseName || 'standby')) {
      btn.classList.add('dh-sp-btn--active');
    } else {
      btn.classList.remove('dh-sp-btn--active');
    }
  });
}

function _toggleSidePanel() {
  if (_state.sidePanelOpen) _closeSidePanel();
  else _openSidePanel();
}

function _openSidePanel() {
  var panel = document.getElementById('dh-sidepanel');
  if (!panel) return;
  _closeQuickMenu();
  _state.sidePanelOpen = true;
  panel.classList.add('dh-sidepanel--open');
  panel.setAttribute('aria-hidden', 'false');
  _updateSidePanelActivePose();
}

function _closeSidePanel() {
  var panel = document.getElementById('dh-sidepanel');
  _state.sidePanelOpen = false;
  if (panel) {
    panel.classList.remove('dh-sidepanel--open');
    panel.setAttribute('aria-hidden', 'true');
  }
}

function _handleSidePanelAction(rawAction) {
  if (!rawAction) return;

  if (rawAction.indexOf('setPose:') === 0) {
    var poseName = rawAction.replace('setPose:', '');
    digitalHumanSetPose(poseName);
    _updateSidePanelActivePose();
    return;
  }
  if (rawAction.indexOf('react:') === 0) {
    var emotion = rawAction.replace('react:', '');
    digitalHumanReact(emotion);
    return;
  }
  if (rawAction.indexOf('micro:') === 0) {
    var microType = rawAction.replace('micro:', '');
    var microPose = 'slight_nod';
    if (microType === 'negative') microPose = 'slight_tilt';
    else if (microType === 'thinking') microPose = 'think';
    digitalHumanMicroExpression(microPose);
    return;
  }
  if (rawAction === 'stopSpeech') {
    digitalHumanStopSpeech({ preserveBubble: true });
    return;
  }
  _handleQuickAction(rawAction);
}

function _sidePanelFlashFeedback(btn) {
  btn.classList.remove('dh-sp-btn--flash');
  void btn.offsetWidth;
  btn.classList.add('dh-sp-btn--flash');
  setTimeout(function () { btn.classList.remove('dh-sp-btn--flash'); }, 300);
}

function _handleQuickAction(action) {
  _closeQuickMenu();

  switch (action) {
    case 'toggleAutoVoice':
      _toggleAutoVoicePreference();
      break;
    case 'toggleSidePanel':
      if (typeof window.togglePracticeSidePanel === 'function') window.togglePracticeSidePanel();
      break;
    case 'newSession':
      if (_state.activePage === 'knowledge_qa') {
        if (typeof window.startNewKnowledgeQaConversation === 'function') window.startNewKnowledgeQaConversation();
      } else if (_state.activePage === 'on_duty_assistant') {
        if (typeof window.clearAssistantSession === 'function') window.clearAssistantSession();
      } else if (_state.activePage === 'quick_query') {
        if (typeof window.startNewQuickQueryConversation === 'function') window.startNewQuickQueryConversation();
      } else {
        if (typeof window.resetPracticeSession === 'function') window.resetPracticeSession();
      }
      break;
    case 'endSession':
      if (typeof window.submitPracticeEnd === 'function') window.submitPracticeEnd();
      break;
    case 'evaluate':
      if (typeof window.submitPracticeEvaluate === 'function') window.submitPracticeEvaluate();
      break;
    case 'replaySummary':
      var replayPage = _state.activePage || 'practical_training';
      var replayText = '';
      if (typeof window.digitalHumanGetPageSpeechText === 'function') {
        replayText = String(window.digitalHumanGetPageSpeechText(replayPage) || '').trim();
      }
      if (!replayText) replayText = _state.lastSummary;
      if (replayText) {
        var isPracticeScore = false;
        if (replayPage === 'practical_training' && window.moduleState && window.moduleState.practice) {
          isPracticeScore = !!window.moduleState.practice.evaluationResult;
        }
        var replayKind = replayPage === 'assessment'
          ? 'exam_coach'
          : (replayPage === 'practical_training' && !isPracticeScore ? 'turn_feedback' : 'score');
        digitalHumanSpeak(replayText, {
          bubbleKind: replayKind,
          pose: _state.targetPoseName || 'confident',
          trigger: 'manual',
          scene: replayPage === 'practical_training'
            ? (isPracticeScore ? 'practice_score' : 'practice_turn_feedback')
            : (replayPage === 'assessment' ? 'assessment' : 'score'),
        });
      }
      break;
    case 'readReply':
      _dhReadLatestReply();
      break;
    case 'dailyBrief':
      _dhDailyBrief();
      break;
    case 'cyclePose':
      digitalHumanCyclePose();
      break;
    case 'examEncourage':
      if (typeof window._dhExamEncourage === 'function') window._dhExamEncourage();
      else digitalHumanSpeak('深呼吸，放松心态，相信你平时的积累。', { bubbleKind: 'greeting', pose: 'encourage' });
      break;
    case 'trainingProgress':
      if (typeof window._dhTrainingProgress === 'function') window._dhTrainingProgress();
      else digitalHumanSpeak('继续加油，每天进步一点点。', { bubbleKind: 'greeting', pose: 'encourage' });
      break;
    case 'narrateLeaderboard':
      if (typeof window._dhNarrateLeaderboard === 'function') window._dhNarrateLeaderboard();
      break;
    case 'skillComment':
      if (typeof window._dhSkillComment === 'function') window._dhSkillComment();
      break;
    case 'narrateQuery':
      if (typeof window._dhNarrateQuery === 'function') window._dhNarrateQuery();
      break;
    case 'riskBrief':
      if (typeof window._dhRiskBrief === 'function') window._dhRiskBrief();
      break;
    case 'recallQuiz':
      if (typeof window._dhRecallQuiz === 'function') window._dhRecallQuiz();
      else digitalHumanSpeak('先打开一篇文档阅读，读完后我来考你。', { bubbleKind: 'greeting', pose: 'reading' });
      break;
    case 'resetView':
      if (_state.avatar) {
        _state.avatar.rotation.x = _state.avatarHomeRotation.x;
        _state.avatar.rotation.y = _state.avatarHomeRotation.y;
        _state.avatar.rotation.z = _state.avatarHomeRotation.z;
      }
      break;
  }
}

function _toggleAutoVoicePreference() {
  var prefs = _getDigitalHumanPrefs();
  var nextMuted = prefs.voice_muted !== true;
  if (typeof window.setDigitalHumanPreferences === 'function') {
    try {
      window.setDigitalHumanPreferences({ voice_muted: nextMuted });
    } catch (e) {}
  } else {
    try {
      localStorage.setItem('digital_human_prefs_v1', JSON.stringify(Object.assign({}, prefs, { voice_muted: nextMuted })));
    } catch (e) {}
  }
  _syncAutoVoiceUi();
  digitalHumanSpeak(nextMuted ? '已开启静音模式，后续仅显示文字提示' : '已关闭静音模式，数字人恢复正常播报', {
    bubbleKind: 'greeting',
    silent: true,
    trigger: 'manual',
    scene: 'greeting',
  });
}

function _dhReadLatestReply() {
  var text = '';
  var page = _state.activePage || '';
  if (typeof window.digitalHumanGetPageSpeechText === 'function') {
    text = String(window.digitalHumanGetPageSpeechText(page) || '').trim();
  }
  if (page === 'on_duty_assistant') {
  } else if (page === 'knowledge_qa') {
    if (!text) {
      var proseNodes = document.querySelectorAll('.qa-answer-prose');
      if (proseNodes.length > 0) {
        var sections = proseNodes[proseNodes.length - 1].querySelectorAll('.qa-answer-paragraph');
        if (sections.length > 0) {
          text = Array.prototype.map.call(sections, function(el) { return el.textContent || ''; }).filter(Boolean).join(' ');
        }
      }
    }
    if (!text) {
      var bubbles = document.querySelectorAll('.knowledge-qa-ai-bubble');
      if (bubbles.length > 0) text = bubbles[bubbles.length - 1].textContent || '';
    }
  }
  if (text) {
    digitalHumanSpeak(text, {
      bubbleKind: page,
      pose: 'agree',
      trigger: 'manual',
      scene: page === 'knowledge_qa' ? 'knowledge_qa' : 'assistant',
    });
  }
}

function _dhDailyBrief() {
  if (typeof window.buildDigitalHumanHomeDirectorPayload === 'function') {
    var directorPayload = window.buildDigitalHumanHomeDirectorPayload();
    if (directorPayload && directorPayload.briefText) {
      digitalHumanSpeak(String(directorPayload.briefText || ''), {
        bubbleKind: 'daily',
        pose: 'agree',
        emotion: 'calm',
        trigger: 'manual',
        scene: 'daily',
      });
      return;
    }
  }
  var xhr = new XMLHttpRequest();
  xhr.open('GET', '/api/dashboard/home-stats', true);
  xhr.setRequestHeader('Content-Type', 'application/json');
  var token = '';
  try { token = localStorage.getItem('token') || ''; } catch (e) {}
  if (token) xhr.setRequestHeader('Authorization', 'Bearer ' + token);
  xhr.onload = function () {
    if (xhr.status !== 200 || !xhr.response) {
      digitalHumanSpeak('暂时无法获取今日播报数据，请稍后再试。', { bubbleKind: 'daily', trigger: 'manual', scene: 'daily' });
      return;
    }
    var res = {};
    try { res = JSON.parse(xhr.responseText); } catch (e) {}
    var d = res.data || {};
    var m = d.metrics || {};

    var parts = [];
    parts.push('早上好！今天是你的数智管家晨会播报。');

    var displayName = '';
    try { displayName = localStorage.getItem('displayName') || ''; } catch (e) {}
    if (displayName) parts.push(displayName + '，');

    var pendingExams = Number(d.pending_exams || m.pending_exams || 0);
    var practiceCount = Number(m.monthly_practice_count || 0);
    var assistantCount = Number(m.monthly_assistant_count || 0);
    var activeCount = Number(m.monthly_active || d.active_employees || 0);

    if (pendingExams > 0) {
      parts.push('你有' + pendingExams + '个待完成的考核任务，记得安排时间完成哦。');
    }
    if (practiceCount > 0) {
      parts.push('本月你已经完成了' + practiceCount + '次智能陪练，');
      if (practiceCount >= 10) {
        parts.push('练习量很充足，继续保持！');
      } else if (practiceCount >= 5) {
        parts.push('保持这个节奏，再多练几次会更好。');
      } else {
        parts.push('建议增加练习频率，熟能生巧。');
      }
    }
    if (assistantCount > 0) {
      parts.push('在岗助手你已经使用了' + assistantCount + '次，遇到问题随时问我。');
    }
    if (activeCount > 0) {
      parts.push('目前团队共有' + activeCount + '名活跃学员在学习。');
    }

    if (parts.length <= 2) {
      parts.push('今天暂时没有新的动态，祝你工作顺利！');
    }

    var greetings = ['新的一天，加油！', '让我们一起变得更好！', '今天也要元气满满哦！'];
    parts.push(greetings[Math.floor(Math.random() * greetings.length)]);

    digitalHumanSpeak(parts.join(''), { bubbleKind: 'daily', pose: 'agree', trigger: 'manual', scene: 'daily' });
  };
  xhr.onerror = function () {
    digitalHumanSpeak('播报数据请求失败，请稍后再试。', { bubbleKind: 'daily', trigger: 'manual', scene: 'daily' });
  };
  xhr.send();
}

function _hitAvatar(event) {
  if (!_state.renderer) return false;

  var canvas = _state.renderer.domElement;
  var rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return false;

  var cx = event.clientX - rect.left;
  var cy = event.clientY - rect.top;
  if (cx < 0 || cy < 0 || cx > rect.width || cy > rect.height) return false;

  var canvasX = Math.round(cx * (canvas.width / rect.width));
  var canvasY = Math.round((canvas.height - 1) - cy * (canvas.height / rect.height));

  var gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
  if (!gl) return false;

  var px = new Uint8Array(4);
  gl.readPixels(canvasX, canvasY, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return px[3] > 20;
}

function _average(parts, path) {
  if (!parts.length) return 0;
  var tokens = path.split('.');
  var total = 0;

  for (var i = 0; i < parts.length; i += 1) {
    var value = parts[i];
    for (var j = 0; j < tokens.length; j += 1) {
      value = value[tokens[j]];
    }
    total += Number(value || 0);
  }

  return total / parts.length;
}

function _menuIconMore() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/></svg>';
}

function _menuIconCycle() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M4 20L21 3"/><path d="M21 16v5h-5"/><path d="M15 15l6 6"/><path d="M4 4l5 5"/></svg>';
}

function _menuIconPanel() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M15 5v14"/></svg>';
}

function _menuIconPlus() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>';
}

function _menuIconClose() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>';
}

function _menuIconCheck() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>';
}

function _menuIconReplay() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.708"/><path d="M3 3v6h6"/></svg>';
}

function _menuIconReset() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>';
}

function _menuIconMute() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5L6 9H3v6h3l5 4z"/><path d="M16 9l5 6"/><path d="M21 9l-5 6"/></svg>';
}

function _menuIconVolume() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5L6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 6a8.5 8.5 0 0 1 0 12"/></svg>';
}

/* ====== Emotion Reaction System ====== */

function digitalHumanReact(emotion) {
  if (!_state.loaded || !_state.practiceActive) return;
  if (!_POSE_PRESETS[emotion]) return;
  if (_state.speaking) {
    _state.pendingEmotion = emotion;
    return;
  }
  _playEmotion(emotion);
}

function _playEmotion(emotion) {
  _clearEmotionTimeout();
  var previousPose = _state.targetPoseName;
  _state.previousPoseName = previousPose;

  digitalHumanSetPose(emotion);

  var texts = _EMOTION_BUBBLES[emotion];
  if (_isPreferenceEnabled('emotion_bubble') && texts && texts.length && !_state.speaking) {
    var text = texts[Math.floor(Math.random() * texts.length)];
    _setBubble(text, 'emotion');
  }

  _state.emotionTimeoutId = setTimeout(function () {
    _state.emotionTimeoutId = null;
    if (_state.speaking) return;
    _clearBubble();
    digitalHumanSetPose(_state.previousPoseName || 'standby');
    _state.previousPoseName = null;
  }, 2800);
}

function _clearEmotionTimeout() {
  if (_state.emotionTimeoutId) {
    clearTimeout(_state.emotionTimeoutId);
    _state.emotionTimeoutId = null;
  }
}

function _startIdleCheck() {
  _stopIdleCheck();
  if (!_isPreferenceEnabled('idle_hint')) return;
  _state.idleCheckInterval = setInterval(function () {
    if (!_state.loaded || !_state.practiceActive || _state.speaking || _state.menuOpen) return;
    var idleMs = Date.now() - _state.lastActivityTime;
    var sinceLastHint = Date.now() - _state.lastIdleHintTime;
    if (idleMs >= _IDLE_THRESHOLD_MS && sinceLastHint >= _IDLE_COOLDOWN_MS) {
      _state.lastIdleHintTime = Date.now();
      _playEmotion('idle_hint');
    }
  }, _IDLE_CHECK_INTERVAL_MS);
}

function _stopIdleCheck() {
  if (_state.idleCheckInterval) {
    clearInterval(_state.idleCheckInterval);
    _state.idleCheckInterval = null;
  }
}

/* ====== Micro-Expression System ====== */

function digitalHumanMicroExpression(type) {
  if (!_state.loaded || !_state.practiceActive || _state.speaking) return;
  if (!_isPreferenceEnabled('micro_expression')) return;
  if (!_POSE_PRESETS[type]) return;
  _clearMicroExpressionTimeout();
  _state.preMicroPoseName = _state.targetPoseName;
  digitalHumanSetPose(type);
  _state.microExpressionTimeoutId = setTimeout(function () {
    _state.microExpressionTimeoutId = null;
    if (_state.speaking || _state.emotionTimeoutId) return;
    digitalHumanSetPose(_state.preMicroPoseName || 'standby');
    _state.preMicroPoseName = null;
  }, _MICRO_EXPRESSION_DURATION_MS);
}

function _clearMicroExpressionTimeout() {
  if (_state.microExpressionTimeoutId) {
    clearTimeout(_state.microExpressionTimeoutId);
    _state.microExpressionTimeoutId = null;
  }
}

/* ====== Speaking Micro-Interaction System ====== */

function _startSpeakingMicro() {
  var sm = _state.speakingMicro;
  sm.active = true;
  sm.startTime = performance.now();
  sm.nextGestureTime = performance.now() + 1500 + Math.random() * 1500;
  sm.currentGesture = null;
}

function _stopSpeakingMicro() {
  _state.speakingMicro.active = false;
  _state.speakingMicro.currentGesture = null;
}

function _applySpeakingMicro(delta) {
  if (!_state.speakingMicro.active || !_state.loaded) return;

  var now = performance.now();
  var elapsed = (now - _state.speakingMicro.startTime) / 1000;
  var cfg = _SPEAKING_MICRO;

  // Continuous subtle oscillations
  var breathe = Math.sin(elapsed * cfg.breatheSpeed * Math.PI * 2) * cfg.breatheAmp;
  var sway = Math.sin(elapsed * cfg.swaySpeed * Math.PI * 2) * cfg.swayAmp;
  var headBob = Math.sin(elapsed * cfg.headBobSpeed * Math.PI * 2) * cfg.headBobAmp;

  // Random gesture scheduling
  var sm = _state.speakingMicro;
  if (!sm.currentGesture && now >= sm.nextGestureTime) {
    sm.currentGesture = _SPEAKING_GESTURES[Math.floor(Math.random() * _SPEAKING_GESTURES.length)];
    sm.gestureStartTime = now;
    sm.nextGestureTime = now + cfg.gestureMinMs + Math.random() * (cfg.gestureMaxMs - cfg.gestureMinMs);
  }

  // Gesture envelope (bell curve: rise then fall)
  var gOff = { headPitch: 0, headYaw: 0, bodyPitch: 0, leftArmRotX: 0, rightArmRotX: 0 };
  if (sm.currentGesture) {
    var gElapsed = now - sm.gestureStartTime;
    var gProgress = Math.min(1, gElapsed / cfg.gestureDurMs);
    var envelope = Math.sin(gProgress * Math.PI);
    var g = sm.currentGesture;
    gOff.headPitch = g.headPitch * envelope;
    gOff.headYaw = g.headYaw * envelope;
    gOff.bodyPitch = g.bodyPitch * envelope;
    gOff.leftArmRotX = g.leftArmRotX * envelope;
    gOff.rightArmRotX = g.rightArmRotX * envelope;
    if (gProgress >= 1) sm.currentGesture = null;
  }

  // Path 1: pose groups (non-animated models)
  if (_state.poseGroups && !_state.mixer) {
    var head = _state.poseGroups.headGroup;
    var upper = _state.poseGroups.upperBodyGroup;
    var leftArm = _state.poseGroups.leftArmGroup;
    var rightArm = _state.poseGroups.rightArmGroup;
    var root = _state.poseGroups.avatarRoot;
    if (upper) {
      upper.rotation.x += breathe + gOff.bodyPitch;
    }
    if (head) {
      head.rotation.x += headBob + gOff.headPitch;
      head.rotation.y += sway * 0.5 + gOff.headYaw;
    }
    if (leftArm) leftArm.rotation.x += sway * 0.3 + gOff.leftArmRotX;
    if (rightArm) rightArm.rotation.x += sway * 0.3 + gOff.rightArmRotX;
    if (root) root.rotation.y += sway;
    return;
  }

  // Path 2: bones for GLB models with skeleton
  var mf = _state.mouseFollow;
  if (mf.bonesFound && _state.mixer) {
    if (mf.headBone) {
      mf.headBone.rotation.x += headBob + gOff.headPitch;
      mf.headBone.rotation.y += sway * 0.5 + gOff.headYaw;
    }
    if (mf.neckBone) {
      mf.neckBone.rotation.x += breathe * 0.5;
      mf.neckBone.rotation.y += sway * 0.3;
    }
    if (mf.spineBone) {
      mf.spineBone.rotation.x += breathe + gOff.bodyPitch;
      mf.spineBone.rotation.y += sway;
    }
    return;
  }

  // Path 3: fallback — subtle whole-avatar sway
  if (_state.avatar && _state.mixer) {
    _state.avatar.rotation.y += sway * 0.3;
  }
}

function digitalHumanAnalyzeReply(text) {
  var t = String(text || '');
  for (var i = 0; i < _MICRO_POSITIVE_WORDS.length; i++) {
    if (t.indexOf(_MICRO_POSITIVE_WORDS[i]) >= 0) return 'slight_nod';
  }
  for (var j = 0; j < _MICRO_NEGATIVE_WORDS.length; j++) {
    if (t.indexOf(_MICRO_NEGATIVE_WORDS[j]) >= 0) return 'slight_tilt';
  }
  for (var k = 0; k < _MICRO_THINKING_WORDS.length; k++) {
    if (t.indexOf(_MICRO_THINKING_WORDS[k]) >= 0) return 'think';
  }
  return '';
}

function getDigitalHumanDebugState() {
  return {
    practiceActive: !!_state.practiceActive,
    mouseFollowEnabled: !!(_state.mouseFollow && _state.mouseFollow.enabled),
    targetHeadYaw: Number((_state.mouseFollow && _state.mouseFollow.targetHeadYaw) || 0),
    targetHeadPitch: Number((_state.mouseFollow && _state.mouseFollow.targetHeadPitch) || 0),
    avatarRotation: _state.avatar ? {
      x: Number(_state.avatar.rotation.x || 0),
      y: Number(_state.avatar.rotation.y || 0),
      z: Number(_state.avatar.rotation.z || 0),
    } : null,
    currentPose: String(_state.targetPoseName || ''),
  };
}

window.renderDigitalHumanWidget = renderDigitalHumanWidget;
window.mountDigitalHuman = mountDigitalHuman;
window.destroyDigitalHuman = destroyDigitalHuman;
window.digitalHumanSpeak = digitalHumanSpeak;
window.digitalHumanStopSpeech = digitalHumanStopSpeech;
window.digitalHumanCollapse = digitalHumanCollapse;
window.digitalHumanExpand = digitalHumanExpand;
window.digitalHumanSetPose = digitalHumanSetPose;
window.digitalHumanCyclePose = digitalHumanCyclePose;
window.digitalHumanSetActivePage = digitalHumanSetActivePage;
window.digitalHumanReact = digitalHumanReact;
window.digitalHumanMicroExpression = digitalHumanMicroExpression;
window.digitalHumanSyncPreferences = digitalHumanSyncPreferences;
window.digitalHumanAnalyzeReply = digitalHumanAnalyzeReply;
window.digitalHumanLoginSuccess = digitalHumanLoginSuccess;
window.digitalHumanLoginFail = digitalHumanLoginFail;
window._DH_ENABLED_PAGES = _DH_ENABLED_PAGES;
window.__getDigitalHumanDebugState = getDigitalHumanDebugState;

// Auto-mount: module scripts are deferred and load after app.js,
// so app.js may have already rendered the page and tried
// to call mountDigitalHuman before this module was ready.
// Note: home page has no hash, so treat empty hash as 'home'.
(function _autoMount() {
  // Check if login view is currently visible (user not authenticated)
  var loginView = document.getElementById('view-login');
  var isLoginPage = loginView && !loginView.classList.contains('hidden');
  if (isLoginPage && !_hasPersistedAuthSession() && _DH_ENABLED_PAGES.indexOf('login') >= 0) {
    digitalHumanSetActivePage('login');
    mountDigitalHuman();
    return;
  }
  var hash = (window.location.hash || '').replace('#', '');
  var pageId = hash || 'home';
  if (_DH_ENABLED_PAGES.indexOf(pageId) >= 0) {
    mountDigitalHuman();
    digitalHumanSetActivePage(pageId);
  }
})();
