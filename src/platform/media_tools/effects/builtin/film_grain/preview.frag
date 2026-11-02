uint lowbias32(uint x) {
    x ^= x >> 16u;
    x *= 0x7feb352du;
    x ^= x >> 15u;
    x *= 0x846ca68bu;
    x ^= x >> 16u;
    return x;
}

float grainNoise(uvec2 cell, uint seed) {
    uint h = lowbias32((cell.x * 73856093u) ^ (cell.y * 19349663u) ^ ((seed + 1u) * 83492791u));
    uint s = lowbias32(h ^ 0x9e3779b9u);
    return float(h) / 4294967296.0 + float(s) / 4294967296.0 - 1.0;
}

uint frameSeed(int channel) {
    uint base = lowbias32(uint(u_seed) ^ lowbias32(uint(u_frame) + 0x632be5abu));
    return lowbias32(base ^ (uint(channel + 1) * 0x27d4eb2du));
}

vec4 effect(vec4 color, vec2 uv) {
    uvec2 cell = uvec2(floor(uv * u_resolution)) / uint(u_cell);
    float luma = dot(color.rgb, vec3(0.299, 0.587, 0.114));
    float t = 2.0 * luma - 1.0;
    float mid = 1.0 - 0.7 * t * t;
    vec3 noise = u_color
        ? vec3(grainNoise(cell, frameSeed(0)), grainNoise(cell, frameSeed(1)), grainNoise(cell, frameSeed(2)))
        : vec3(grainNoise(cell, frameSeed(0)));
    vec3 delta = noise * u_strength * mid / 255.0;
    return vec4(clamp(color.rgb + delta, 0.0, 1.0), color.a);
}
