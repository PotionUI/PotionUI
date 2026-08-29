<script lang="ts">
	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();
</script>

<div class="bg-surface-1 rounded-lg border border-line shadow-raised">
	<div class="px-6 py-3 border-b border-line">
		<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Access</h3>
	</div>
	<div class="px-6 divide-y divide-line">
		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="allow-registration" class="block text-sm font-medium text-fg mb-1">
					Allow anyone to register
				</label>
				<p class="text-sm text-fg-muted">
					When off, only the owner can create accounts in Administration → Users.
				</p>
			</div>
			<input
				type="checkbox"
				id="allow-registration"
				class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
				checked={settings.registration_policy === 'open'}
				onchange={(e) =>
					onSettingChange('registration_policy', e.currentTarget.checked ? 'open' : 'closed')}
			/>
		</div>

		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="mcp-enabled" class="block text-sm font-medium text-fg mb-1">MCP Access</label>
				<p class="text-sm text-fg-muted">
					Allow external MCP clients to connect and act as PotionUI users via their tokens
				</p>
			</div>
			<input
				type="checkbox"
				id="mcp-enabled"
				class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
				checked={settings.mcp_enabled || false}
				onchange={(e) => onSettingChange('mcp_enabled', e.currentTarget.checked)}
			/>
		</div>
	</div>
</div>
