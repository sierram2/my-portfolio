AOS.init();

const glitterName = document.getElementById('glitter-name');

function createGlitterParticle() {
  const glitter = document.createElement('div');
  glitter.classList.add('glitter-dot');

  glitter.style.left = (Math.random() * glitterName.offsetWidth) + 'px';
  glitter.style.top = (Math.random() * glitterName.offsetHeight * 0.6) + 'px';

  glitter.style.setProperty('--size', (Math.random() * 3 + 2) + 'px');
  glitter.style.setProperty('--opacity', (Math.random() * 0.5 + 0.5));

  const x = (Math.random() - 0.5) * 80 + 'px';
  const y = (Math.random() - 0.5) * 80 + 'px';

  glitter.style.setProperty('--x', x);
  glitter.style.setProperty('--y', y);

  glitterName.appendChild(glitter);
  glitter.addEventListener('animationend', () => glitter.remove());
}

// Glitter on hover
glitterName.addEventListener('mouseenter', () => {
  let count = 0;
  const glitterBurst = setInterval(() => {
    createGlitterParticle();
    count++;
    if (count > 20) clearInterval(glitterBurst);
  }, 60);
});

// Glitter burst + redirect on click
glitterName.addEventListener('click', () => {
  let count = 0;
  const burst = setInterval(() => {
    createGlitterParticle();
    count++;
    if (count > 25) {
      clearInterval(burst);
      setTimeout(() => {
        window.location.href = 'index.html';
      }, 300);
    }
  }, 20);
});
