// ===========================================================
//  IKK VPN — кинематографичный интерактив
//  • шапка становится «стеклянной» при прокрутке
//  • меню-гамбургер на мобильных
//  • блоки плавно поднимаются при появлении на экране
//  • видео-фон уважает «уменьшить движение»
// ===========================================================

const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ============ 1. Шапка и герой при прокрутке ============ */
const navbar = document.querySelector('.navbar');
const heroVideo = document.querySelector('.page-hero .hero-video');
const heroSection = heroVideo ? heroVideo.closest('.page-hero') : null;

function onScroll() {
    const y = window.scrollY;
    if (navbar) navbar.classList.toggle('scrolled', y > 16);

    // видео-герой динамично «уезжает» вверх, открывая звёздный фон;
    // вуаль (::after) едет вместе с ним через --heroShift, чтобы под
    // волной оставался чистый фон без полосы другого тона
    if (heroVideo && heroSection && !reduceMotion) {
        const p = Math.min(y / (heroSection.offsetHeight || 1), 1);
        const shift = (-p * 36).toFixed(2) + '%';
        heroVideo.style.transform = `translateY(${shift})`;
        heroVideo.style.opacity = (1 - p * 0.35).toFixed(3);
        heroSection.style.setProperty('--heroShift', shift);
    }
}
window.addEventListener('scroll', onScroll, { passive: true });
onScroll();

/* ============ 2. Меню-гамбургер ============ */
const burger = document.querySelector('.burger');
const navLinks = document.querySelector('.nav-links');
if (burger && navLinks) {
    burger.addEventListener('click', () => navLinks.classList.toggle('open'));
    navLinks.querySelectorAll('a').forEach((l) =>
        l.addEventListener('click', () => navLinks.classList.remove('open')));
}

/* ============ 3. Появление блоков при прокрутке ============ */
const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
        if (e.isIntersecting) {
            // карточки в сетке появляются по очереди
            const el = e.target;
            if (el.classList.contains('card')) {
                const idx = [...el.parentNode.children].indexOf(el);
                el.style.transitionDelay = (idx * 0.08) + 's';
                clearTimeout(el._dt);
                el._dt = setTimeout(() => { el.style.transitionDelay = ''; }, 900 + idx * 80);
            }
            el.classList.add('visible');
            revealObserver.unobserve(el);   // показываем один раз — без мигания
        }
    });
}, { threshold: 0.12 });
document.querySelectorAll('.reveal').forEach((el) => revealObserver.observe(el));

/* ============ 4. Живой фон: звёздное небо с параллаксом ============ */
(function initStars() {
    const canvas = document.getElementById('stars');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let W = 0, H = 0, stars = [];

    function resize() {
        W = canvas.width = window.innerWidth;
        H = canvas.height = window.innerHeight;
        const count = Math.min(240, Math.round((W * H) / 8500));
        stars = Array.from({ length: count }, () => ({
            x: Math.random() * W,
            y: Math.random() * H,
            r: Math.random() * 1.3 + 0.3,
            depth: Math.random() * 0.8 + 0.2,       // дальние — тусклее и медленнее
            tw: Math.random() * Math.PI * 2,        // фаза мерцания
            sp: Math.random() * 0.02 + 0.005,       // скорость мерцания
            vx: (Math.random() - 0.5) * 0.05,       // лёгкий дрейф в сторону
        }));
    }
    resize();
    window.addEventListener('resize', resize);

    function frame() {
        ctx.clearRect(0, 0, W, H);
        const sy = window.scrollY;
        for (const s of stars) {
            s.tw += s.sp;
            s.x += s.vx;
            if (s.x < 0) s.x += W; else if (s.x > W) s.x -= W;
            // параллакс прокрутки: звёзды «плывут» медленнее контента
            let y = (s.y - sy * 0.14 * s.depth) % H;
            if (y < 0) y += H;
            const a = (0.4 + Math.sin(s.tw) * 0.3) * s.depth;
            ctx.globalAlpha = Math.max(0.05, a);
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.arc(s.x, y, s.r, 0, Math.PI * 2);
            ctx.fill();
        }
        ctx.globalAlpha = 1;
        if (!reduceMotion) requestAnimationFrame(frame);
    }
    frame();   // при «уменьшить движение» рисуем один статичный кадр
})();

/* ============ 5. Видео-фон и «уменьшить движение» ============ */
document.querySelectorAll('video.hero-video').forEach((v) => {
    if (reduceMotion) {
        v.removeAttribute('autoplay');
        v.pause();
        return;
    }
    // страховка автоплея: после медленной загрузки браузер может
    // не запустить отложенное видео сам — подталкиваем вручную
    const kick = () => { if (v.paused) v.play().catch(() => {}); };
    v.addEventListener('canplay', kick);
    kick();
});
