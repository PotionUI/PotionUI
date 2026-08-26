<script lang="ts">
	import { page } from '$app/stores';

	$: currentPath = $page.url.pathname;

	$: isActive = (itemPath: string): boolean => {
		return currentPath === itemPath || currentPath.startsWith(itemPath + '/');
	};

	const icons: Record<string, string> = {
		sparkles:
			'M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z',
		clock: 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z',
		cube: 'M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9',
		cog: 'M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 010 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 010-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28z M15 12a3 3 0 11-6 0 3 3 0 016 0z'
	};

	interface TabItem {
		path: string;
		label: string;
		icon: string;
	}

	const tabs: TabItem[] = [
		{ path: '/generate', label: 'Generate', icon: 'sparkles' },
		{ path: '/history', label: 'History', icon: 'clock' },
		{ path: '/models', label: 'Models', icon: 'cube' },
		{ path: '/settings', label: 'Settings', icon: 'cog' }
	];
</script>

<!-- The safe-area padding must ADD to the 64px content box, not eat into it —
	with a fixed h-16 the home-indicator inset squeezes the tabs into ~30px on
	notched phones, so the height carries the inset explicitly. -->
<nav
	class="fixed bottom-0 left-0 right-0 bg-canvas border-t border-line z-50 flex items-stretch md:hidden"
	style="height: calc(4rem + env(safe-area-inset-bottom)); padding-bottom: env(safe-area-inset-bottom);"
>
	{#each tabs as tab}
		<a
			href={tab.path}
			class="relative flex-1 flex flex-col items-center justify-center gap-1 transition-all duration-150 ease-out active:scale-95
				{isActive(tab.path) ? 'text-signal' : 'text-fg-subtle hover:text-fg-muted'}"
		>
			<svg class="w-6 h-6 transition-colors duration-150" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d={icons[tab.icon]}
				/>
			</svg>
			<span class="text-[10px] font-medium leading-none transition-colors duration-150">{tab.label}</span>
			<span class="absolute bottom-0 left-2 right-2 h-0.5 rounded-t transition-all duration-150 ease-out {isActive(tab.path) ? 'bg-signal opacity-100' : 'bg-transparent opacity-0'}"></span>
		</a>
	{/each}
</nav>
