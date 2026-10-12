// Godot 4 canvas-item shader: one 2D light over a sprite + its normal map.
//
// The uniforms match SpriteIlluminator's own light model (CodeAndWeb REA run,
// reports/03_spriteilluminator.md): `brightness` is the 0..100 property value,
// colour alpha is intensity, and there is no specular/gloss term. The parameter
// JSON written by `forge lit --engine godot` feeds these values directly.
// Parses clean on Godot 4.7.2 with the GL Compatibility renderer (checked
// headless by tests/test_normalmap.py); the game currently targets 4.6, across
// which these uniforms and built-ins are unchanged.
//
// Attach to a Sprite2D (or any CanvasItem) and assign its normal map.
shader_type canvas_item;

uniform sampler2D normal_map : hint_normal;
uniform vec2  light_pos = vec2(0.5, 0.5);              // normalized, y grows down
uniform float light_height = 100.0;                    // SI light_height, in pixels
uniform vec4  light_color : source_color = vec4(1.0);
uniform float brightness = 100.0;                      // SI light_brightness (0..100)
uniform vec4  ambient_color : source_color = vec4(0.5, 0.5, 0.5, 0.5);
uniform float normal_strength = 1.0;                   // flatten the relief if needed

void fragment() {
	vec4 tex = texture(TEXTURE, UV);
	vec3 n = normalize(texture(normal_map, UV).rgb * 2.0 - 1.0);
	n.xy *= normal_strength;

	// screen space grows downward; flip Y so a light above the sprite brightens its top
	vec2 to_light = vec2(light_pos.x - UV.x, -(light_pos.y - UV.y));
	vec3 L = normalize(vec3(to_light, light_height * 0.01));
	float d = max(dot(n, L), 0.0);

	vec3 amb = ambient_color.rgb * ambient_color.a;
	vec3 lit = light_color.rgb * light_color.a * (brightness / 100.0) * d;
	COLOR = vec4(tex.rgb * (amb + lit), tex.a);
}
