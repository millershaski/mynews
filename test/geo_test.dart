import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:my_news/globe/geo.dart';

Country square(String code, double lon0, double lat0, double lon1, double lat1) => Country(
      code,
      code,
      (lon0 + lon1) / 2,
      (lat0 + lat1) / 2,
      [Float64List.fromList([lon0, lat0, lon1, lat0, lon1, lat1, lon0, lat1])],
    );

void main() {
  group('normalizeLon', () {
    test('wraps into [-180, 180)', () {
      expect(normalizeLon(0), 0);
      expect(normalizeLon(190), -170);
      expect(normalizeLon(-190), 170);
      expect(normalizeLon(540), -180);
      expect(normalizeLon(-45), -45);
    });
  });

  group('Ortho', () {
    test('the centre projects to the origin and inverts back', () {
      final o = Ortho(10, 20, const Offset(100, 100), 50);
      final (p, visible) = o.project(10, 20);
      expect(visible, isTrue);
      expect(p.dx, closeTo(100, 1e-9));
      expect(p.dy, closeTo(100, 1e-9));
      expect(o.invert(const Offset(100, 100)), (10.0, 20.0));
    });

    test('project and invert round-trip for visible points', () {
      final o = Ortho(-100, 40, const Offset(200, 200), 150);
      for (final (lon, lat) in [(-100.0, 40.0), (-80.0, 30.0), (-120.0, 60.0), (-95.0, 10.0), (-60.0, 45.0)]) {
        final (p, visible) = o.project(lon, lat);
        expect(visible, isTrue, reason: '($lon, $lat)');
        final back = o.invert(p)!;
        expect(back.$1, closeTo(lon, 1e-6));
        expect(back.$2, closeTo(lat, 1e-6));
      }
    });

    test('north is up on screen', () {
      final o = Ortho(0, 0, const Offset(0, 0), 100);
      expect(o.project(0, 30).$1.dy, lessThan(0));
      expect(o.project(30, 0).$1.dx, greaterThan(0));
    });

    test('the far side is hidden and points off the disc do not invert', () {
      final o = Ortho(0, 0, const Offset(0, 0), 100);
      expect(o.project(180, 0).$2, isFalse);
      expect(o.invert(const Offset(150, 0)), isNull);
    });
  });

  group('Country', () {
    final a = square('AA', 0, 0, 10, 10);
    final b = square('BB', 20, 0, 30, 10);

    test('contains uses the polygon, not just the bounds', () {
      final triangle = Country('TT', 'Tri', 1, 1, [Float64List.fromList([0, 0, 10, 0, 0, 10])]);
      expect(triangle.contains(1, 1), isTrue);
      expect(triangle.contains(9, 9), isFalse); // inside the bounding box, outside the triangle
      expect(triangle.contains(-1, 1), isFalse);
    });

    test('holes are excluded by the even-odd rule', () {
      final ring = Country('RR', 'Ring', 0, 0, [
        Float64List.fromList([0, 0, 10, 0, 10, 10, 0, 10]),
        Float64List.fromList([4, 4, 6, 4, 6, 6, 4, 6]),
      ]);
      expect(ring.contains(2, 2), isTrue);
      expect(ring.contains(5, 5), isFalse);
    });

    test('countryAt finds the country under a point', () {
      expect(countryAt([a, b], 5, 5)?.code, 'AA');
      expect(countryAt([a, b], 25, 5)?.code, 'BB');
      expect(countryAt([a, b], 15, 5), isNull);
    });

    test('the bundled outlines place well-known points correctly', () {
      final countries = Country.parse(File('assets/countries.json').readAsStringSync());
      expect(countries.length, greaterThan(150));
      expect(countryAt(countries, 2.35, 48.86)?.code, 'FR'); // Paris
      expect(countryAt(countries, 139.69, 35.69)?.code, 'JP'); // Tokyo
      expect(countryAt(countries, -47.9, -15.8)?.code, 'BR'); // Brasília
      expect(countryAt(countries, -98.0, 39.0)?.code, 'US'); // Kansas
      expect(countryAt(countries, -30.0, 0.0), isNull); // mid-Atlantic
    });
  });

  group('seasons', () {
    test('hemispheres have opposite seasons', () {
      expect(seasonAt(45, 1), Season.winter);
      expect(seasonAt(-45, 1), Season.summer);
      expect(seasonAt(45, 7), Season.summer);
      expect(seasonAt(-45, 7), Season.winter);
      expect(seasonAt(45, 4), Season.spring);
      expect(seasonAt(-45, 4), Season.autumn);
      expect(seasonAt(45, 10), Season.autumn);
      expect(seasonAt(-45, 10), Season.spring);
    });

    test('northern snow reaches much of North America in January but not July', () {
      expect(snowLineNorth(1), lessThan(45)); // e.g. reaches the northern US
      expect(snowLineNorth(7), greaterThan(70)); // only the high Arctic
      expect(snowLineNorth(1), lessThan(snowLineNorth(7)));
    });

    test('southern snow is greatest in the southern winter', () {
      expect(snowLineSouth(7), greaterThan(snowLineSouth(1))); // closer to the equator in July
      expect(snowLineSouth(1), lessThan(0));
    });

    test('sea ice peaks in late winter of each hemisphere', () {
      final north = [for (var m = 1; m <= 12; m++) seaIceNorth(m)];
      final south = [for (var m = 1; m <= 12; m++) seaIceSouth(m)];
      expect(north.indexOf(north.reduce((x, y) => x < y ? x : y)) + 1, 3); // lowest latitude edge in March
      expect(south.indexOf(south.reduce((x, y) => x > y ? x : y)) + 1, 9); // closest to equator in September
    });

    test('caption names both hemispheres', () {
      expect(seasonCaption(1), 'January: winter in the north, summer in the south');
    });

    test('ice sheets stay white all year', () {
      final gl = square('GL', -50, 60, -30, 80);
      expect(landColor(gl, 7), iceColor);
      final temperate = square('XX', 0, 40, 10, 50);
      expect(landColor(temperate, 1), isNot(landColor(temperate, 7)));
    });
  });
}
