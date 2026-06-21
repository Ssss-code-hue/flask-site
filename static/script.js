// ===========================================================
//  Kirill.dev — винтажный чёрно-белый интерактив
//  • Three.js: монохромная «пыль плёнки» в 3D (реагирует на скролл/курсор)
//  • свет за курсором + наклон карточек + проявление блоков
// ===========================================================

let mx = 0, my = 0, tmx = 0, tmy = 0;   // сглаженная позиция курсора (-1..1)
let scrollY = 0, scrollTarget = 0;

const spotlight = document.querySelector('.spotlight');

/* --- курсор: двигаем свет + цель для параллакса фона --- */
window.addEventListener('mousemove', (e) => {
    tmx = (e.clientX / window.innerWidth) * 2 - 1;
    tmy = (e.clientY / window.innerHeight) * 2 - 1;
    if (spotlight) {
        spotlight.style.setProperty('--mx', e.clientX + 'px');
        spotlight.style.setProperty('--my', e.clientY + 'px');
    }
}, { passive: true });

/* ============ 0. Винтажные розетки-печати (гильош) ============ */
function makeRosetteCanvas() {
    const S = 600, c = document.createElement('canvas');
    c.width = c.height = S;
    const x = c.getContext('2d'), cx = S / 2, cy = S / 2;
    x.strokeStyle = '#9a968c'; x.lineWidth = 1;
    const rings = [
        { R: 285, amp: 16, k: 28 }, { R: 240, amp: 12, k: 20 },
        { R: 195, amp: 22, k: 34 }, { R: 150, amp: 10, k: 16 },
        { R: 108, amp: 18, k: 24 },
    ];
    rings.forEach((rg, idx) => {
        x.beginPath();
        for (let a = 0; a <= Math.PI * 2 + 0.02; a += 0.01) {
            const r = rg.R + rg.amp * Math.sin(rg.k * a + idx);
            const px = cx + Math.cos(a) * r, py = cy + Math.sin(a) * r;
            a === 0 ? x.moveTo(px, py) : x.lineTo(px, py);
        }
        x.stroke();
    });
    [60, 300].forEach((r) => { x.beginPath(); x.arc(cx, cy, r, 0, Math.PI * 2); x.stroke(); });
    return c;
}
(function initMarks() {
    const marks = [...document.querySelectorAll('.vintage-mark')];
    if (!marks.length) return;
    const url = makeRosetteCanvas().toDataURL();
    marks.forEach((m) => { m.style.backgroundImage = `url(${url})`; });
})();

/* ============ 1. 3D-частицы (пыль плёнки) ============ */
(function initDust() {
    const host = document.getElementById('bg');
    if (!host || !window.THREE) return;

    // мягкая круглая точка
    function discTexture() {
        const c = document.createElement('canvas');
        c.width = c.height = 64;
        const g = c.getContext('2d').createRadialGradient(32, 32, 0, 32, 32, 32);
        g.addColorStop(0, 'rgba(255,255,255,1)');
        g.addColorStop(0.4, 'rgba(255,255,255,0.6)');
        g.addColorStop(1, 'rgba(255,255,255,0)');
        const ctx = c.getContext('2d');
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, 64, 64);
        return new THREE.CanvasTexture(c);
    }

    const W = window.innerWidth, H = window.innerHeight;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(60, W / H, 0.1, 100);
    camera.position.z = 18;

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(W, H);
    host.appendChild(renderer.domElement);

    const COUNT = 420;
    const positions = new Float32Array(COUNT * 3);
    for (let i = 0; i < COUNT; i++) {
        positions[i * 3]     = (Math.random() - 0.5) * 60;
        positions[i * 3 + 1] = (Math.random() - 0.5) * 60;
        positions[i * 3 + 2] = (Math.random() - 0.5) * 35;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));

    const mat = new THREE.PointsMaterial({
        size: 0.22, map: discTexture(), transparent: true,
        opacity: 0.55, depthWrite: false, sizeAttenuation: true,
        color: 0xece9e1,
    });
    const dust = new THREE.Points(geo, mat);
    scene.add(dust);

    // смена цвета пыли под тему (тёмная пыль на белом фоне)
    window.__setDustTheme = (light) => {
        mat.color.set(light ? 0x1a1712 : 0xece9e1);
        mat.opacity = light ? 0.5 : 0.55;
    };

    function render() {
        requestAnimationFrame(render);
        mx += (tmx - mx) * 0.04;
        my += (tmy - my) * 0.04;
        scrollY += (scrollTarget - scrollY) * 0.07;

        // курсор слегка двигает камеру (параллакс при наведении на фон)
        camera.position.x = mx * 4;
        camera.position.y = -my * 4 + scrollY * 0.012;   // + параллакс при скролле
        camera.lookAt(0, scrollY * 0.012, 0);

        dust.rotation.y += 0.0004;
        dust.rotation.z = scrollY * 0.0002;
        renderer.render(scene, camera);
    }
    render();

    window.addEventListener('resize', () => {
        const w = window.innerWidth, h = window.innerHeight;
        camera.aspect = w / h; camera.updateProjectionMatrix();
        renderer.setSize(w, h);
    });
})();

/* ============ 2. Скролл: прогресс, шапка и круговой переход темы ============ */
const navbar = document.querySelector('.navbar');
const progress = document.querySelector('.progress');
const whySection = document.getElementById('why');
const syncReveals = [...document.querySelectorAll('.sync')];   // появляются вместе с переходом
let isLight = false;

// единственный переход: тёмная ⇄ светлая тема (круг на CSS + цвета + пыль)
function setLight(on) {
    if (on === isLight) return;
    isLight = on;
    document.body.classList.toggle('light', on);
    if (window.__setDustTheme) window.__setDustTheme(on);
}

function onScroll() {
    const y = window.scrollY;
    scrollTarget = y;
    if (navbar) navbar.classList.toggle('scrolled', y > 16);
    if (progress) {
        const max = document.documentElement.scrollHeight - window.innerHeight;
        progress.style.width = (max > 0 ? (y / max) * 100 : 0) + '%';
    }
    // переход И появление «Почему IKK» синхронизированы у границы hero → секция
    const vh = window.innerHeight;
    const trigger = whySection ? whySection.offsetTop - vh * 0.5 : vh * 0.6;
    if (!isLight && y > trigger) {
        setLight(true);
        syncReveals.forEach((e) => e.classList.add('visible'));
    } else if (isLight && y < trigger - vh * 0.12) {
        setLight(false);
        syncReveals.forEach((e) => e.classList.remove('visible'));
    }
}
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();

/* ============ 3. Меню-гамбургер ============ */
const burger = document.querySelector('.burger');
const navLinks = document.querySelector('.nav-links');
if (burger && navLinks) {
    burger.addEventListener('click', () => navLinks.classList.toggle('open'));
    navLinks.querySelectorAll('a').forEach((l) =>
        l.addEventListener('click', () => navLinks.classList.remove('open')));
}

/* ============ 4. Появление блоков при прокрутке ============ */
const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
        if (!e.isIntersecting) return;
        const el = e.target;
        // карточки появляются по очереди (ступенчато), потом задержку сбрасываем,
        // чтобы она не мешала наклону при наведении
        if (el.classList.contains('card')) {
            const idx = [...el.parentNode.children].indexOf(el);
            el.style.transitionDelay = (idx * 0.12) + 's';
            requestAnimationFrame(() => el.classList.add('visible'));
            setTimeout(() => { el.style.transitionDelay = ''; }, 1000 + idx * 120);
        } else {
            el.classList.add('visible');
        }
        revealObserver.unobserve(el);
    });
}, { threshold: 0.15 });
document.querySelectorAll('.reveal:not(.sync)').forEach((el) => revealObserver.observe(el));

/* ============ 5. Карточки: 3D-наклон + свет за курсором ============ */
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
document.querySelectorAll('.card').forEach((card) => {
    card.addEventListener('mousemove', (e) => {
        const r = card.getBoundingClientRect();
        const px = e.clientX - r.left, py = e.clientY - r.top;
        card.style.setProperty('--mx', `${px}px`);
        card.style.setProperty('--my', `${py}px`);
        if (reduceMotion) return;
        const rotX = ((py / r.height) - 0.5) * -9;
        const rotY = ((px / r.width) - 0.5) * 9;
        card.style.transform = `perspective(1000px) rotateX(${rotX}deg) rotateY(${rotY}deg) translateY(-4px)`;
    });
    card.addEventListener('mouseleave', () => { card.style.transform = ''; });
});