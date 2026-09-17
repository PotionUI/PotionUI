<script>
  export let context = {};
  export let hookName = '';
  export let pluginId = '';

  let fetching = false;

  $: model = context.model || {};
  $: eligible = !!model.sha256 && !model.is_directory;

  function notify(level, message) {
    const notifications = window.__potionui?.notifications;
    if (notifications?.toast) {
      notifications.toast(level, message);
    }
  }

  async function handleFetch(event) {
    event.stopPropagation();
    if (fetching || !eligible || !model.id) return;
    fetching = true;

    try {
      const token = localStorage.getItem('auth_token');
      const response = await fetch('/api/models/info/fetch', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {})
        },
        body: JSON.stringify({
          provider: 'civitai',
          model_ids: [model.id],
          force_refresh: false
        })
      });

      if (!response.ok) {
        throw new Error(`Fetch failed (${response.status})`);
      }

      notify('info', 'CivitAI fetch started. Reload the list to see the result.');
    } catch (e) {
      notify('error', 'Fetching CivitAI data failed');
    } finally {
      fetching = false;
    }
  }
</script>

{#if eligible}
  <button
    class="bg-black/60 hover:bg-black/80 text-white rounded p-1.5 backdrop-blur-sm transition-opacity duration-100 opacity-0 group-hover:opacity-100 disabled:opacity-50"
    on:click={handleFetch}
    disabled={fetching}
    aria-label="Fetch CivitAI data"
    title="Fetch CivitAI data"
  >
    {#if fetching}
      <svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
    {:else}
      <svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
      </svg>
    {/if}
  </button>
{/if}
