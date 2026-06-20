// ===========================================================
//  Kirill.dev 🌴 — тропический интерактив
//  • Пальмовый лист рисуется процедурно на canvas
//  • Three.js: листья двигаются в 3D-пространстве при скролле
//  • Карточки: 3D-наклон + свечение за курсором
// ===========================================================

/* ============ 1. Рисуем пальмовый лист на canvas ============ */
function makePalmCanvas() {
    const c = document.createElement('canvas');
    c.width = 512; c.height = 512;
    const x = c.getContext('2d');

    const P0 = { x: 256, y: 500 };   // основание (черешок)
    const P1 = { x: 210, y: 250 };   // изгиб
    const P2 = { x: 250, y: 40 };    // кончик

    // точка и касательная на квадратичной кривой Безье
    const pt = (t) => ({
        x: (1 - t) ** 2 * P0.x + 2 * (1 - t) * t * P1.x + t ** 2 * P2.x,
        y: (1 - t) ** 2 * P0.y + 2 * (1 - t) * t * P1.y + t ** 2 * P2.y,
    });
    const tangent = (t) => {
        const dx = 2 * (1 - t) * (P1.x - P0.x) + 2 * t * (P2.x - P1.x);
        const dy = 2 * (1 - t) * (P1.y - P0.y) + 2 * t * (P2.y - P1.y);
        return Math.atan2(dy, dx);
    };

    // зелёный градиент листа
    const g = x.createLinearGradient(0, 500, 0, 30);
    g.addColorStop(0, '#0a6b3f');
    g.addColorStop(0.55, '#1ba85f');
    g.addColorStop(1, '#9be15d');
    x.fillStyle = g;
    x.strokeStyle = g;

    // одно перо листа (leaflet)
    function leaflet(bx, by, ang, len, wid) {
        const tx = bx + Math.cos(ang) * len;
        const ty = by + Math.sin(ang) * len;
        const perp = ang + Math.PI / 2;
        const mx = bx + Math.cos(ang) * len * 0.45;
        const my = by + Math.sin(ang) * len * 0.45;
        x.beginPath();
        x.moveTo(bx, by);
        x.quadraticCurveTo(mx + Math.cos(perp) * wid, my + Math.sin(perp) * wid, tx, ty);
        x.quadraticCurveTo(mx - Math.cos(perp) * wid, my - Math.sin(perp) * wid, bx, by);
        x.fill();
    }

    const N = 26;
    for (let i = 1; i < N; i++) {
        const t = i / N;
        const b = pt(t);
        const a = tangent(t);                 // направление "вверх по стеблю"
        const len = 150 * Math.sin(Math.PI * t) ** 0.7 + 18;
        const wid = len * 0.16;
        const spread = 1.0;                    // угол отхождения пера
        leaflet(b.x, b.y, a - spread, len, wid);  // левая сторона
        leaflet(b.x, b.y, a + spread, len, wid);  // правая сторона
    }

    // центральный стебель поверх
    x.lineWidth = 7; x.lineCap = 'round';
    x.beginPath();
    x.moveTo(P0.x, P0.y);
    x.quadraticCurveTo(P1.x, P1.y, P2.x, P2.y);
    x.stroke();

    return c;
}

const palmCanvas = makePalmCanvas();
const palmURL = palmCanvas.toDataURL();

/* ============ 2. Декоративные пальмы по углам ============ */
const decos = [...document.querySelectorAll('.palm-deco')];
const baseRot = { 'palm-tl': 25, 'palm-tr': 110, 'palm-bl': -60, 'palm-br': 200 };
decos.forEach((d) => { d.style.backgroundImage = `url(${palmURL})`; });

/* ============ 3. 3D-фон из пальм (Three.js) ============ */
let leaves = [];
let scrollY = 0, scrollTarget = 0;

(function initBackground() {
    const host = document.getElementById('bg');
    if (!host || !window.THREE) return;

    const W = window.innerWidth, H = window.innerHeight;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, W / H, 0.1, 100);
    camera.position.z = 10;

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(W, H);
    host.appendChild(renderer.domElement);

    const texture = new THREE.CanvasTexture(palmCanvas);
    texture.anisotropy = 4;

    // создаём множество листьев на разной глубине
    const COUNT = 16;
    for (let i = 0; i < COUNT; i++) {
        const mat = new THREE.MeshBasicMaterial({
            map: texture, transparent: true, depthWrite: false,
            opacity: 0.45 + Math.random() * 0.4,
        });
        const size = 3 + Math.random() * 5;
        const mesh = new THREE.Mesh(new THREE.PlaneGeometry(size, size), mat);

        const depth = -8 + Math.random() * 12;         // z-глубина
        mesh.position.set(
            (Math.random() - 0.5) * 22,
            (Math.random() - 0.5) * 16,
            depth
        );
        mesh.rotation.z = Math.random() * Math.PI * 2;

        mesh.userData = {
            baseY: mesh.position.y,
            baseRot: mesh.rotation.z,
            // ближе к камере (больше depth) → сильнее параллакс
            parallax: 0.4 + (depth + 8) / 12 * 1.4,
            swayAmp: 0.05 + Math.random() * 0.12,
            swaySpeed: 0.3 + Math.random() * 0.6,
            phase: Math.random() * Math.PI * 2,
        };
        scene.add(mesh);
        leaves.push(mesh);
    }

    const clock = new THREE.Clock();
    function render() {
        requestAnimationFrame(render);
        const t = clock.getElapsedTime();
        scrollY += (scrollTarget - scrollY) * 0.07;   // плавная прокрутка

        leaves.forEach((m) => {
            const u = m.userData;
            // движение фона при скролле (параллакс по глубине)
            m.position.y = u.baseY + scrollY * 0.012 * u.parallax;
            // лёгкое покачивание
            m.rotation.z = u.baseRot + Math.sin(t * u.swaySpeed + u.phase) * u.swayAmp;
        });
        renderer.render(scene, camera);
    }
    render();

    window.addEventListener('resize', () => {
        const w = window.innerWidth, h = window.innerHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
    });
})();

/* ============ 4. Скролл: цель параллакса + угловые пальмы + шапка ============ */
const navbar = document.querySelector('.navbar');

function onScroll() {
    scrollTarget = window.scrollY;
    if (navbar) navbar.classList.toggle('scrolled', window.scrollY > 20);

    // угловые пальмы плавно сдвигаются (параллакс)
    decos.forEach((d, i) => {
        const cls = [...d.classList].find((c) => c.startsWith('palm-') && c !== 'palm-deco');
        const rot = baseRot[cls] || 0;
        const dir = i % 2 === 0 ? 1 : -1;
        const shift = window.scrollY * 0.06 * dir;
        d.style.transform = `translateY(${shift}px) rotate(${rot + window.scrollY * 0.01 * dir}deg)`;
    });
}
window.addEventListener('scroll', onScroll, { passive: true });
// задаём стартовый поворот углов
decos.forEach((d) => {
    const cls = [...d.classList].find((c) => c.startsWith('palm-') && c !== 'palm-deco');
    d.style.transform = `rotate(${baseRot[cls] || 0}deg)`;
});

/* ============ 4b. Плавающие лепестки 🌸 + солнечные блики ✨ ============ */
(function initFX() {
    const fx = document.getElementById('fx');
    if (!fx || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const rand = (a, b) => a + Math.random() * (b - a);
    // тропические цвета лепестков (пары для градиента)
    const palette = [
        ['#fda4af', '#fb7185'], // коралл
        ['#f9a8d4', '#f472b6'], // розовый
        ['#fde68a', '#fbbf24'], // золото
        ['#fecdd3', '#fda4af'], // нежно-розовый
        ['#fef3c7', '#fcd34d'], // светло-жёлтый
    ];

    // --- лепестки ---
    for (let i = 0; i < 18; i++) {
        const p = document.createElement('div');
        p.className = 'petal';
        const c = palette[Math.floor(Math.random() * palette.length)];
        p.style.setProperty('--x', rand(0, 100) + 'vw');
        p.style.setProperty('--size', rand(10, 22) + 'px');
        p.style.setProperty('--c1', c[0]);
        p.style.setProperty('--c2', c[1]);
        p.style.setProperty('--dur', rand(9, 18) + 's');
        p.style.setProperty('--delay', -rand(0, 18) + 's');   // отрицательная задержка = старт вразнобой
        p.style.setProperty('--sway', rand(-140, 140) + 'px');
        p.style.setProperty('--op', rand(0.45, 0.85).toFixed(2));
        fx.appendChild(p);
    }

    // --- солнечные блики (больше у солнца, в правом верхнем углу) ---
    for (let i = 0; i < 14; i++) {
        const g = document.createElement('div');
        g.className = 'glint';
        const nearSun = Math.random() < 0.6;
        g.style.setProperty('--x', (nearSun ? rand(55, 98) : rand(0, 100)) + 'vw');
        g.style.setProperty('--y', (nearSun ? rand(0, 40) : rand(0, 100)) + 'vh');
        g.style.setProperty('--g', rand(4, 10) + 'px');
        g.style.setProperty('--op', rand(0.6, 1).toFixed(2));
        g.style.setProperty('--tdur', rand(2.5, 5) + 's');
        g.style.setProperty('--tdelay', -rand(0, 5) + 's');
        fx.appendChild(g);
    }
})();

/* ============ 5. Меню-гамбургер ============ */
const burger = document.querySelector('.burger');
const navLinks = document.querySelector('.nav-links');
if (burger && navLinks) {
    burger.addEventListener('click', () => navLinks.classList.toggle('open'));
    navLinks.querySelectorAll('a').forEach((l) =>
        l.addEventListener('click', () => navLinks.classList.remove('open')));
}

/* ============ 6. Появление блоков при прокрутке ============ */
const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
        if (e.isIntersecting) { e.target.classList.add('visible'); revealObserver.unobserve(e.target); }
    });
}, { threshold: 0.15 });
document.querySelectorAll('.reveal').forEach((el) => revealObserver.observe(el));

/* ============ 7. Карточки: 3D-наклон + свечение ============ */
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
document.querySelectorAll('.card').forEach((card) => {
    card.addEventListener('mousemove', (e) => {
        const r = card.getBoundingClientRect();
        const px = e.clientX - r.left, py = e.clientY - r.top;
        card.style.setProperty('--mx', `${px}px`);
        card.style.setProperty('--my', `${py}px`);
        if (reduceMotion) return;
        const rotX = ((py / r.height) - 0.5) * -10;
        const rotY = ((px / r.width) - 0.5) * 10;
        card.style.transform = `perspective(900px) rotateX(${rotX}deg) rotateY(${rotY}deg) translateY(-6px)`;
    });
    card.addEventListener('mouseleave', () => { card.style.transform = ''; });
});
