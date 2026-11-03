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

vec3 vignette(vec3 v, vec2 pixel, float amount, float midpoint, float feather, float k) {
    float a = amount / 100.0 * k;
    float e0 = midpoint / 100.0;
    float e1 = e0 + max(0.05, feather / 100.0);
    float r = length(pixel + 0.5 - u_resolution * 0.5) / length(u_resolution) * 2.0;
    float t = clamp((r - e0) / (e1 - e0), 0.0, 1.0);
    float w = t * t * (3.0 - 2.0 * t);
    vec3 x = v / 255.0;
    vec3 o = a >= 0.0 ? x * (1.0 - a * w) : x + (1.0 - x) * -a * w;
    return floor(clamp(o, 0.0, 1.0) * 255.0 + 0.5);
}

vec3 grain(vec3 v, vec2 pixel, float amount, float size, float seed, float k) {
    float a = amount / 100.0 * k;
    float cell = max(1.0, floor(size * min(u_resolution.x, u_resolution.y) / 1000.0 + 0.5));
    float noise = grainNoise(uvec2(floor(pixel / cell)), uint(seed));
    float luma = dot(v, vec3(0.2126, 0.7152, 0.0722)) / 255.0;
    float t = 2.0 * luma - 1.0;
    float mid = 1.0 - 0.7 * t * t;
    return clamp(floor(v + noise * a * 0.14 * mid * 255.0 + 0.5), 0.0, 255.0);
}

vec3 spatial(vec3 v, vec2 pixel, float kind, float a, float b, float c, float k) {
    if (kind == 1.0) return vignette(v, pixel, a, b, c, k);
    if (kind == 2.0) return grain(v, pixel, a, b, c, k);
    return v;
}

vec4 effect(vec4 color, vec2 uv) {
    vec3 v = floor(color.rgb * 255.0 + 0.5);
    if (u_amount <= 0.0) return vec4(v / 255.0, color.a);
    float n = float(u_table_size);
    vec3 x = v / 255.0;
    vec3 graded = texture(u_table, (x * (n - 1.0) + 0.5) / n).rgb;
    v = clamp(floor((x + u_amount * (graded - x)) * 255.0 + 0.0001), 0.0, 255.0);
    vec2 pixel = floor(uv * u_resolution);
    v = spatial(v, pixel, u_s0_kind, u_s0_a, u_s0_b, u_s0_c, u_amount);
    v = spatial(v, pixel, u_s1_kind, u_s1_a, u_s1_b, u_s1_c, u_amount);
    v = spatial(v, pixel, u_s2_kind, u_s2_a, u_s2_b, u_s2_c, u_amount);
    v = spatial(v, pixel, u_s3_kind, u_s3_a, u_s3_b, u_s3_c, u_amount);
    return vec4(v / 255.0, color.a);
}
