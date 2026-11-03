import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
	capitalize,
	formatBytes,
	formatCount,
	formatDate,
	formatSpeed,
	modelTypeLabel,
	percent,
	placeholderStyle,
	plural,
	thumbUrl
} from './format.js';
import { installHostModelTypes } from './testModelTypes.js';

test('formatBytes uses binary units with one decimal', () => {
	assert.equal(formatBytes(239_600_000), '228.5 MB');
	assert.equal(formatBytes(1536), '1.5 KB');
	assert.equal(formatBytes(1024), '1.0 KB');
	assert.equal(formatBytes(6_938_040_682), '6.5 GB');
	assert.equal(formatBytes(2 * 1024 ** 4), '2.0 TB');
});

test('formatBytes keeps small sizes in bytes and moves 1023.96 KB up a unit', () => {
	assert.equal(formatBytes(0), '0 B');
	assert.equal(formatBytes(512), '512 B');
	assert.equal(formatBytes(1023), '1023 B');
	assert.equal(formatBytes(1024 * 1024 - 10), '1.0 MB');
});

test('formatBytes returns an empty string for what is not a size', () => {
	for (const value of [null, undefined, -1, Number.NaN, Infinity, '12', {}]) {
		assert.equal(formatBytes(value), '', String(value));
	}
});

test('formatCount shortens large counts', () => {
	assert.equal(formatCount(0), '0');
	assert.equal(formatCount(999), '999');
	assert.equal(formatCount(1000), '1k');
	assert.equal(formatCount(1234), '1.2k');
	assert.equal(formatCount(9_999), '10k');
	assert.equal(formatCount(12_345), '12k');
	assert.equal(formatCount(457_123), '457k');
	assert.equal(formatCount(999_499), '999k');
	assert.equal(formatCount(999_500), '1M');
	assert.equal(formatCount(1_500_000), '1.5M');
	assert.equal(formatCount(23_000_000), '23M');
	assert.equal(formatCount(2_100_000_000), '2.1B');
});

test('formatCount returns an empty string for what is not a count', () => {
	for (const value of [null, undefined, -5, Number.NaN, '3']) {
		assert.equal(formatCount(value), '', String(value));
	}
});

test('formatSpeed appends per second, and is empty for nothing', () => {
	assert.equal(formatSpeed(4_404_019), '4.2 MB/s');
	assert.equal(formatSpeed(0), '');
	assert.equal(formatSpeed(null), '');
});

test('percent turns the core progress fraction into a clamped whole percentage', () => {
	assert.equal(percent(0), 0);
	assert.equal(percent(0.4567), 45);
	assert.equal(percent(1), 100);
	assert.equal(percent(1.2), 100);
	assert.equal(percent(-0.1), 0);
	assert.equal(percent(null), null);
	assert.equal(percent('0.5'), null);
});

test('formatDate formats ISO dates and ignores what is not one', () => {
	const day = new Date('2026-10-11T08:30:00Z').toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
	assert.equal(formatDate('2026-10-11T08:30:00Z'), day);
	assert.equal(formatDate(''), '');
	assert.equal(formatDate(null), '');
	assert.equal(formatDate('not a date'), '');
});

test('plural picks the singular for one', () => {
	assert.equal(plural(1, 'file'), '1 file');
	assert.equal(plural(3, 'file'), '3 files');
	assert.equal(plural(0, 'entry', 'entries'), '0 entries');
});

test('placeholderStyle is deterministic and uses the app palette tokens', () => {
	const a = placeholderStyle('Detail Tweaker');
	assert.equal(a, placeholderStyle('Detail Tweaker'));
	assert.match(a, /^background: linear-gradient\(-?\d+deg, rgb\(var\(--viz-[1-8]\) \/ 0\.12\), transparent 65%\), rgb\(var\(--surface-3\)\);$/);
	assert.equal(typeof placeholderStyle(undefined), 'string');
});

test('thumbUrl asks the CivitAI CDN for a resized image', () => {
	const original = 'https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/8d6a57a7-a913-499b-b854-1b8637a3dc00/original=true/188.jpeg';
	assert.equal(
		thumbUrl(original, 450),
		'https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/8d6a57a7-a913-499b-b854-1b8637a3dc00/width=450/188.jpeg'
	);
	const sized = 'https://image.civitai.com/a/b/width=1024,optimized=true/9.jpeg';
	assert.equal(thumbUrl(sized, 320), 'https://image.civitai.com/a/b/width=320/9.jpeg');
});

test('thumbUrl leaves other urls, videos and odd paths alone', () => {
	const hf = 'https://huggingface.co/org/repo/resolve/main/sample.png';
	assert.equal(thumbUrl(hf, 450), hf);
	const video = 'https://image.civitai.com/a/b/original=true/7.mp4';
	assert.equal(thumbUrl(video, 450, 'video'), video);
	const short = 'https://image.civitai.com/a/7.jpeg';
	assert.equal(thumbUrl(short, 450), short);
	const plainHttp = 'http://image.civitai.com/a/b/original=true/7.jpeg';
	assert.equal(thumbUrl(plainHttp, 450), plainHttp);
	assert.equal(thumbUrl('not a url', 450), 'not a url');
	assert.equal(thumbUrl(null, 450), null);
	assert.equal(thumbUrl(hf, 0), hf);
});

test('capitalize and modelTypeLabel', () => {
	installHostModelTypes();
	assert.equal(capitalize('checkpoint'), 'Checkpoint');
	assert.equal(capitalize(''), '');
	assert.equal(modelTypeLabel('lora'), 'LoRA');
	assert.equal(modelTypeLabel('checkpoint'), 'Base model');
	assert.equal(modelTypeLabel('pose_rig'), 'Pose rig');
	assert.equal(modelTypeLabel('detection_bbox'), 'Box detector');
	assert.equal(modelTypeLabel('hologram_rig'), 'Hologram rig');
	assert.equal(modelTypeLabel(null), '');
});
