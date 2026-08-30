<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { TutorialSection } from '$lib/types/tutorial';
	import BaseModal from './BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';

	export let isOpen: boolean = false;
	export let title: string = 'Tutorial';
	export let sections: TutorialSection[] = [];

	const dispatch = createEventDispatcher();

	function handleClose() {
		dispatch('close');
	}

	type Variant = 'default' | 'tip' | 'warning';

	const variantStyles: Record<Variant, string> = {
		default: 'bg-surface-2 border-line-strong',
		tip: 'bg-info/10 border-info/25',
		warning: 'bg-warning/10 border-warning/25'
	};

	const variantIconColor: Record<Variant, string> = {
		default: 'text-fg-muted',
		tip: 'text-info',
		warning: 'text-warning'
	};

	const iconNameMap: Record<string, string> = {
		lightbulb: 'lightbulb',
		warning: 'warning',
		info: 'info',
		check: 'check',
		star: 'star'
	};

	function getVariantStyles(variant?: Variant) {
		return variantStyles[variant ?? 'default'];
	}

	function getVariantIconColor(variant?: Variant) {
		return variantIconColor[variant ?? 'default'];
	}

	function getDefaultIcon(variant?: Variant): string {
		switch (variant) {
			case 'tip':
				return 'lightbulb';
			case 'warning':
				return 'warning';
			default:
				return 'info';
		}
	}

	function getSectionIcon(section: TutorialSection): string {
		if (section.icon && iconNameMap[section.icon]) return iconNameMap[section.icon];
		if (section.icon) return 'info';
		return getDefaultIcon(section.variant);
	}

	// Simple markdown-like formatting
	function formatContent(content: string): string {
		// Bold: **text**
		content = content.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
		// Italic: *text*
		content = content.replace(/\*(.+?)\*/g, '<em>$1</em>');
		// Code: `code`
		content = content.replace(/`(.+?)`/g, '<code class="bg-surface-3 px-1.5 py-0.5 rounded text-sm font-mono">$1</code>');
		// Line breaks
		content = content.replace(/\n/g, '<br>');
		return content;
	}
</script>

<BaseModal {isOpen} {title} sizeClass="md:w-[600px] md:max-h-[85vh]" on:close={handleClose}>
	<svelte:fragment slot="headerIcon">
		<Icon name="book" className="w-5 h-5 text-fg-muted flex-shrink-0" />
	</svelte:fragment>

	<div class="p-4 md:p-6">
		{#if sections.length === 0}
			<div class="text-center text-fg-muted py-8">
				<p>No tutorial content available.</p>
			</div>
		{:else}
			<div class="space-y-4">
				{#each sections as section}
					<div class="border rounded-lg p-4 {getVariantStyles(section.variant)}">
						<div class="flex items-start gap-3">
							<div class="flex-shrink-0 mt-0.5">
								<Icon name={getSectionIcon(section)} className="w-5 h-5 {getVariantIconColor(section.variant)}" />
							</div>
							<div class="flex-1 min-w-0">
								<h3 class="text-sm font-semibold text-fg mb-2">
									{section.title}
								</h3>
								<div class="text-sm text-fg-muted leading-relaxed overflow-x-auto">
									{@html formatContent(section.content)}
								</div>
							</div>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	<svelte:fragment slot="footer">
		<div class="px-4 py-3 md:px-6 md:py-4 flex justify-end">
			<Button variant="primary" onclick={handleClose}>Got it</Button>
		</div>
	</svelte:fragment>
</BaseModal>
