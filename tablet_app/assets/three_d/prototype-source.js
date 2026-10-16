import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const status = document.querySelector('#status');
const button = document.querySelector('#speak');
const motionButtons = [...document.querySelectorAll('[data-motion]')];
const modelPicker = document.querySelector('#model');
let vrm, context, analyser, source, samples, decodedAudio;
let mouth = 0, nextBlink = 3, blinkStart = -10, elapsed = 0;
let gesture = null, nextIdleGesture = 10;
const bones = {};
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(28, innerWidth / innerHeight, .1, 100);
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 1.25));
renderer.setSize(innerWidth, innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.enablePan = false;
controls.minDistance = 2.4;
controls.maxDistance = 5;
controls.minPolarAngle = Math.PI * .35;
controls.maxPolarAngle = Math.PI * .58;
controls.minAzimuthAngle = -Math.PI * .42;
controls.maxAzimuthAngle = Math.PI * .42;
scene.add(new THREE.HemisphereLight(0xffffff, 0x806040, 2));
const light = new THREE.DirectionalLight(0xffffff, 2);
light.position.set(-2, 4, 3);
scene.add(light);

function reset() {
  camera.position.set(0, .95, 3.9);
  controls.target.set(0, .9, 0);
  controls.update();
}
reset();
document.querySelector('#reset').onclick = reset;
function report(message) { status.textContent = message; console.log('[3D]', message); }
addEventListener('error', e => report('3D错误：' + e.message));
addEventListener('unhandledrejection', e => report('3D错误：' + e.reason));
function playGesture(name) {
  if (!vrm) return;
  gesture = { name, start: elapsed, duration: name === 'wave' ? 4.5 : name === 'nod' ? 2.6 : 4 };
  nextIdleGesture = elapsed + 12;
}
motionButtons.forEach(b => { b.onclick = () => playGesture(b.dataset.motion); });
const loader = new GLTFLoader();
loader.register(p => new VRMLoaderPlugin(p));
async function loadModel() {
  modelPicker.disabled = true;
  button.disabled = true;
  motionButtons.forEach(b => { b.disabled = true; });
  report('正在加载' + modelPicker.selectedOptions[0].textContent + '…');
  try {
    const gltf = await loader.loadAsync(modelPicker.value);
    const next = gltf.userData.vrm;
    if (!next) throw Error('文件没有 VRM 数据');
    VRMUtils.rotateVRM0(next);
    if (vrm) {
      scene.remove(vrm.scene);
      VRMUtils.deepDispose(vrm.scene);
    }
    vrm = next;
    scene.add(vrm.scene);
    if (modelPicker.value === 'AvatarSample_B_teacher-experiment.vrm') {
      applyTeacherColorExperiment(vrm);
    }
    for (const name of ['hips', 'spine', 'chest', 'neck', 'head', 'leftUpperArm', 'rightUpperArm',
      'leftLowerArm', 'rightLowerArm', 'leftHand', 'rightHand']) {
      bones[name] = vrm.humanoid.getNormalizedBoneNode(name);
    }
    reset();
    report('已加载 · 可拖动视角，点击挥手/点头/讲解');
    playGesture('wave');
  } catch (e) {
    report('模型加载失败：' + e.message);
  } finally {
    modelPicker.disabled = false;
    button.disabled = !vrm;
    motionButtons.forEach(b => { b.disabled = !vrm; });
  }
}
modelPicker.onchange = loadModel;
loadModel();

// Runtime-only tint experiment. The original meshes, bones and expressions
// remain untouched; this measures the safe customization boundary before a
// real clothing edit in Blender or Unity.
function applyTeacherColorExperiment(model) {
  model.scene.traverse(object => {
    if (!object.isMesh) return;
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    materials.forEach(material => {
      const name = material?.name ?? '';
      if (name.includes('Tops')) material.color?.set('#e8e2d9');
      if (name.includes('Bottoms')) material.color?.set('#727783');
      if (name.includes('Shoes')) material.color?.set('#353c48');
    });
  });
}

button.onclick = async () => {
  try {
    context ??= new AudioContext();
    await context.resume();
    if (source) { source.stop(); return; }
    button.disabled = true;
    if (!decodedAudio) {
      const response = await fetch('test.wav');
      if (!response.ok) throw Error('测试音频缺失');
      decodedAudio = await context.decodeAudioData(await response.arrayBuffer());
    }
    analyser = context.createAnalyser();
    analyser.fftSize = 512;
    samples = new Float32Array(512);
    source = context.createBufferSource();
    source.buffer = decodedAudio;
    source.connect(analyser);
    analyser.connect(context.destination);
    source.onended = () => {
      source.disconnect(); analyser.disconnect(); source = null; analyser = null;
      button.textContent = '播放测试语音';
    };
    source.start();
    button.textContent = '停止测试语音';
    playGesture('explain');
  } catch (e) { report('音频失败：' + e.message); }
  finally { button.disabled = false; }
};

const smooth = x => { x = THREE.MathUtils.clamp(x, 0, 1); return x * x * (3 - 2 * x); };
function rotate(name, x, y, z) { bones[name]?.rotation.set(x, y, z); }
function animateBody() {
  // Normalized VRM bones let these poses work independently of model-specific axes.
  rotate('hips', 0, Math.sin(elapsed * .45) * .05, Math.sin(elapsed * .7) * .012);
  rotate('spine', Math.sin(elapsed * 1.5) * .009, Math.sin(elapsed * .45) * .025, 0);
  rotate('chest', Math.sin(elapsed * 1.5) * .012, 0, 0);
  rotate('neck', 0, Math.sin(elapsed * .6) * .04, 0);
  rotate('head', Math.sin(elapsed * .8) * .025, Math.sin(elapsed * .6) * .065, Math.sin(elapsed * .5) * .025);
  rotate('leftUpperArm', .03, 0, -1.25);
  rotate('rightUpperArm', .03, 0, 1.25);
  rotate('leftLowerArm', -.06, 0, -.09);
  rotate('rightLowerArm', -.06, 0, .09);
  rotate('leftHand', 0, 0, 0); rotate('rightHand', 0, 0, 0);
  if (!gesture && elapsed > nextIdleGesture) playGesture('nod');
  if (!gesture) return;
  const t = elapsed - gesture.start;
  const weight = smooth(t / .6) * smooth((gesture.duration - t) / .6);
  if (t > gesture.duration) {
    gesture = null;
    if (source) playGesture('explain');
    return;
  }
  if (gesture.name === 'wave') {
    rotate('leftUpperArm', -.25 * weight, 0, -1.25 + 1.05 * weight);
    rotate('leftLowerArm', -.15 * weight, 0, -.09 + 2.25 * weight);
    rotate('leftHand', 0, Math.sin(t * 8) * .3 * weight, Math.sin(t * 8) * .16 * weight);
    rotate('head', 0, -.08 * weight, -.06 * weight);
  } else if (gesture.name === 'nod') {
    rotate('head', (1 - Math.cos(t * Math.PI * 2)) * .08 * weight, 0, 0);
  } else {
    const beat = Math.sin(t * 2.5) * weight;
    rotate('rightUpperArm', -.25 * weight, -.15 * weight, 1.25 - .55 * weight);
    rotate('rightLowerArm', -.35 * weight, .3 * weight, .09 - .85 * weight + beat * .1);
    rotate('rightHand', 0, .35 * weight, -.1 * weight);
    rotate('chest', .02 * weight, -.12 * weight, 0);
    rotate('head', beat * .04, .08 * weight, 0);
  }
}
let previous = 0, frames = 0, measured = 0;
function frame(now) {
  requestAnimationFrame(frame);
  if (document.hidden || now - previous < 32) return;
  const dt = Math.min((now - previous) / 1000, .1);
  previous = now; elapsed += dt;
  if (vrm) {
    if (elapsed > nextBlink) { blinkStart = elapsed; nextBlink = elapsed + 3 + Math.random() * 3; }
    const b = elapsed - blinkStart;
    vrm.expressionManager.setValue('blink', b < .22 ? Math.sin(b / .22 * Math.PI) : 0);
    let volume = 0;
    if (analyser) {
      analyser.getFloatTimeDomainData(samples);
      volume = Math.sqrt(samples.reduce((sum, x) => sum + x * x, 0) / samples.length);
    }
    mouth += (Math.min(.5, Math.max(0, (volume - .012) * 4)) - mouth) * .45;
    vrm.expressionManager.setValue('aa', mouth);
    vrm.expressionManager.setValue('happy', gesture?.name === 'wave' ? .15 : .03);
    animateBody();
    vrm.update(dt);
  }
  controls.update();
  renderer.render(scene, camera);
  frames++; measured += dt;
  if (measured > 5 && vrm) {
    report(`本地3D · ${Math.round(frames / measured)} FPS · 拖动查看侧面`);
    frames = 0; measured = 0;
  }
}
requestAnimationFrame(frame);
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
