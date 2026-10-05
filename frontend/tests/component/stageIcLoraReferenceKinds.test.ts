import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('IC-LoRA reference slot', () => {
	it('accepts video references as well as images', () => {
		const src = readFileSync(resolve(process.cwd(), 'src/lib/components/video-director/stage-rail/StageIcLora.svelte'), 'utf8');
		expect(src).toContain("accept: 'image/*,video/*'");
		expect(src).toMatch(/IC_LORA_REFERENCE_KINDS = \['image', 'video'\]/);
		expect(src).not.toContain('kind="image"');
	});
});
