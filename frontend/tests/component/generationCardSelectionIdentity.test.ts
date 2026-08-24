// @vitest-environment jsdom
//
// The card's carousel walks a FILTERED (`is_final`, nsfw) and RE-ORDERED
// (images→videos→audio→mesh) media list, while consumers hold the raw
// `generation.files`. Emitting a position into the former and resolving it
// against the latter picks the wrong file as soon as one row is hidden - and a
// non-final tmp file is exactly the kind of thing that later gets cleaned up.
// So `onSelect` must hand over the file RECORD. Mounting is the only way to
// exercise it: the filtering lives inline in GenerationCard.svelte.
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationThumbnailURL: vi.fn(() => '/thumb.png')
	}
}));

const { default: GenerationCard } = await import('$lib/components/GenerationCard.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function baseGeneration(files: Record<string, unknown>[]) {
	return {
		id: 'gen-selection',
		form_data: {},
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files,
		rating: 0,
		is_favorite: false
	};
}

function mountCard(files: Record<string, unknown>[]) {
	const received: unknown[] = [];
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationCard as never,
		target,
		props: {
			generation: baseGeneration(files) as never,
			selectable: true,
			onSelect: ((_gen: unknown, file: unknown) => received.push(file)) as never
		}
	});
	return {
		received,
		card: () => target.querySelector<HTMLElement>('[role="button"]') ?? target.firstElementChild,
		nextButton: () => target.querySelector<HTMLButtonElement>('[aria-label="Next image"]'),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountCard> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('GenerationCard selection identity', () => {
	it('emits the file record itself, not a position in its filtered carousel', () => {
		// Index 0 of `files` is a non-final tmp file the card never shows. An
		// index-based contract would report 0 and the consumer would resolve it
		// to `tmp.png`; the identity contract reports the file the user saw.
		mounted = mountCard([
			{
				id: 11,
				file_type: 'image',
				is_final: false,
				file_path: 'generations/2026-01-01/gen-selection/tmp.png',
				created_at: '2026-01-01T00:00:00Z'
			},
			{
				id: 12,
				file_type: 'image',
				is_final: true,
				file_path: 'generations/2026-01-01/gen-selection/1.png',
				created_at: '2026-01-01T00:00:00Z'
			}
		]);

		(mounted.card() as HTMLElement).click();

		expect(mounted.received).toHaveLength(1);
		expect(mounted.received[0]).toMatchObject({
			id: 12,
			file_path: 'generations/2026-01-01/gen-selection/1.png'
		});
	});

	it('emits the video the carousel advanced to, not its position after reordering', async () => {
		// `mediaFiles` puts images before videos, so the video the user lands on
		// after one "next" sits at carousel position 1 but at `files` position 0.
		mounted = mountCard([
			{
				id: 21,
				file_type: 'video',
				is_final: true,
				file_path: 'generations/2026-01-01/gen-selection/1.mp4',
				created_at: '2026-01-01T00:00:00Z'
			},
			{
				id: 22,
				file_type: 'image',
				is_final: true,
				file_path: 'generations/2026-01-01/gen-selection/0.png',
				created_at: '2026-01-01T00:00:00Z'
			}
		]);

		mounted.nextButton()?.click();
		await tick();
		(mounted.card() as HTMLElement).click();

		expect(mounted.received).toHaveLength(1);
		expect(mounted.received[0]).toMatchObject({
			id: 21,
			file_path: 'generations/2026-01-01/gen-selection/1.mp4'
		});
	});
});
