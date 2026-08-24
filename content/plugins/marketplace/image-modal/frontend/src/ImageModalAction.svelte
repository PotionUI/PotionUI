<script>
  import { onMount, onDestroy } from 'svelte';

  export let context = {};
  export let hookName = '';
  export let pluginId = '';

  let showModal = false;
  let currentImage = null;
  let portalContainer = null;

  onMount(() => {
    // Create a portal container at document body level
    portalContainer = document.createElement('div');
    portalContainer.id = 'image-modal-portal';
    document.body.appendChild(portalContainer);
  });

  onDestroy(() => {
    if (portalContainer && portalContainer.parentNode) {
      portalContainer.parentNode.removeChild(portalContainer);
    }
  });

  function openModal() {
    currentImage = context.imageUrl || context.currentImage || context.image;
    if (currentImage) {
      showModal = true;
      renderModal();
    }
  }

  function closeModal() {
    showModal = false;
    if (portalContainer) {
      portalContainer.innerHTML = '';
    }
  }

  function renderModal() {
    if (!portalContainer || !currentImage) return;

    portalContainer.innerHTML = `
      <div class="image-modal-overlay" style="
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.95);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 99999;
        animation: imageModalFadeIn 0.2s ease;
      ">
        <button class="image-modal-close" style="
          position: absolute;
          top: 20px;
          right: 20px;
          background: none;
          border: none;
          color: white;
          font-size: 40px;
          cursor: pointer;
          line-height: 1;
          padding: 0;
          width: 40px;
          height: 40px;
          opacity: 0.8;
          transition: opacity 0.15s ease;
        " aria-label="Close modal">×</button>
        <img src="${currentImage}" alt="Full screen view" style="
          max-width: 95vw;
          max-height: 95vh;
          object-fit: contain;
          animation: imageModalScaleIn 0.2s ease;
        " />
      </div>
      <style>
        @keyframes imageModalFadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes imageModalScaleIn {
          from { transform: scale(0.95); opacity: 0; }
          to { transform: scale(1); opacity: 1; }
        }
        .image-modal-close:hover { opacity: 1 !important; }
      </style>
    `;

    // Add click handlers
    const overlay = portalContainer.querySelector('.image-modal-overlay');
    const closeBtn = portalContainer.querySelector('.image-modal-close');

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeModal();
    });
    closeBtn.addEventListener('click', closeModal);
  }

  function handleKeydown(e) {
    if (e.key === 'Escape' && showModal) closeModal();
  }
</script>

<svelte:window on:keydown={handleKeydown} />

<button class="plugin-btn" on:click={openModal} title="View in full screen">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
  </svg>
</button>

<style>
  .plugin-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 8px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    background: transparent;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .plugin-btn:hover {
    background: rgba(0, 0, 0, 0.05);
  }
</style>
