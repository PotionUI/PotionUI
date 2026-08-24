<script>
  export let context = {};
  export let hookName = '';
  export let pluginId = '';

  let exporting = false;

  $: isImage = (context.fileType || '').toLowerCase() === 'image';

  function notifyError(message) {
    const notifications = window.__potionui?.notifications;
    if (notifications?.toast) {
      notifications.toast('error', message);
    }
  }

  async function handleExport() {
    if (exporting || !context.generationId) return;
    exporting = true;

    try {
      const token = localStorage.getItem('auth_token');
      const params = new URLSearchParams({
        generation_id: context.generationId,
        index: String(context.fileIndex ?? 0)
      });
      const response = await fetch(`/api/plugins/civitai-provider/export-png?${params}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {}
      });

      if (!response.ok) {
        throw new Error(`Export failed (${response.status})`);
      }

      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = blobUrl;
      link.download = context.filename || 'export.png';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (e) {
      notifyError('Export for Civitai failed');
    } finally {
      exporting = false;
    }
  }
</script>

{#if isImage}
  <button
    on:click={handleExport}
    disabled={exporting}
    class="bg-black/50 hover:bg-black/70 disabled:opacity-50 text-white p-3 rounded-lg shadow-lg backdrop-blur-sm transition-colors"
    title="Export for Civitai"
  >
    <svg class="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
    </svg>
  </button>
{/if}
