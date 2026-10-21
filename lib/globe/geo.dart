import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';
import 'dart:ui';

import 'package:flutter/services.dart' show rootBundle;

/// One country outline from assets/countries.json (simplified Natural Earth shapes).
class Country {
  final String code;
  final String name;
  final double labelLon;
  final double labelLat;

  /// Each ring is [lon0, lat0, lon1, lat1, ...] in degrees.
  final List<Float64List> rings;
  final Rect bounds; // left=minLon, top=minLat, right=maxLon, bottom=maxLat

  Country(this.code, this.name, this.labelLon, this.labelLat, this.rings) : bounds = _bounds(rings);

  static Rect _bounds(List<Float64List> rings) {
    var minX = 999.0, minY = 999.0, maxX = -999.0, maxY = -999.0;
    for (final r in rings) {
      for (var i = 0; i + 1 < r.length; i += 2) {
        minX = math.min(minX, r[i]);
        maxX = math.max(maxX, r[i]);
        minY = math.min(minY, r[i + 1]);
        maxY = math.max(maxY, r[i + 1]);
      }
    }
    return Rect.fromLTRB(minX, minY, maxX, maxY);
  }

  /// Even-odd ray casting over all rings, in plain lon/lat space.
  bool contains(double lon, double lat) {
    if (lon < bounds.left || lon > bounds.right || lat < bounds.top || lat > bounds.bottom) return false;
    var inside = false;
    for (final r in rings) {
      final n = r.length ~/ 2;
      for (var i = 0, j = n - 1; i < n; j = i++) {
        final xi = r[2 * i], yi = r[2 * i + 1], xj = r[2 * j], yj = r[2 * j + 1];
        if ((yi > lat) != (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
      }
    }
    return inside;
  }

  static List<Country> parse(String json) {
    final list = jsonDecode(json) as List;
    return [
      for (final c in list.cast<Map<String, dynamic>>())
        Country(
          c['c'] as String,
          c['n'] as String,
          (c['lx'] as num).toDouble(),
          (c['ly'] as num).toDouble(),
          [for (final p in (c['p'] as List)) Float64List.fromList((p as List).map((v) => (v as num).toDouble()).toList())],
        )
    ]..sort((a, b) => a.name.compareTo(b.name));
  }

  static Future<List<Country>> load() async => parse(await rootBundle.loadString('assets/countries.json'));
}

Country? countryAt(List<Country> countries, double lon, double lat) {
  for (final c in countries) {
    if (c.contains(lon, lat)) return c;
  }
  return null;
}

const double _deg = math.pi / 180;

/// Orthographic projection of the globe centred on (centerLon, centerLat).
class Ortho {
  final double centerLon;
  final double centerLat;
  final Offset origin;
  final double radius;
  final double _sinLat0, _cosLat0;

  Ortho(this.centerLon, this.centerLat, this.origin, this.radius)
      : _sinLat0 = math.sin(centerLat * _deg),
        _cosLat0 = math.cos(centerLat * _deg);

  /// Projects a point. Points on the far side are pushed onto the globe's edge and reported as hidden.
  (Offset, bool) project(double lon, double lat) {
    final la = lat * _deg, dl = (lon - centerLon) * _deg;
    final cosLa = math.cos(la);
    var x = cosLa * math.sin(dl);
    var y = _cosLat0 * math.sin(la) - _sinLat0 * cosLa * math.cos(dl);
    final cosC = _sinLat0 * math.sin(la) + _cosLat0 * cosLa * math.cos(dl);
    final visible = cosC >= 0;
    if (!visible) {
      final len = math.sqrt(x * x + y * y);
      if (len > 0) {
        x /= len;
        y /= len;
      }
    }
    return (Offset(origin.dx + radius * x, origin.dy - radius * y), visible);
  }

  /// Screen point back to (lon, lat), or null when it is off the globe.
  (double, double)? invert(Offset p) {
    final x = (p.dx - origin.dx) / radius, y = -(p.dy - origin.dy) / radius;
    final rho = math.sqrt(x * x + y * y);
    if (rho > 1) return null;
    if (rho == 0) return (centerLon, centerLat);
    final c = math.asin(rho);
    final sinC = math.sin(c), cosC = math.cos(c);
    final lat = math.asin(cosC * _sinLat0 + y * sinC * _cosLat0 / rho);
    final lon = centerLon * _deg + math.atan2(x * sinC, rho * cosC * _cosLat0 - y * sinC * _sinLat0);
    return (normalizeLon(lon / _deg), lat / _deg);
  }

  /// Builds a screen path for lon/lat rings. Returns null when every ring is on the far side.
  Path? ringsPath(List<Float64List> rings) {
    final path = Path();
    var any = false;
    for (final r in rings) {
      final n = r.length ~/ 2;
      if (n < 3) continue;
      final pts = List<Offset>.filled(n, Offset.zero);
      var ringVisible = false;
      for (var i = 0; i < n; i++) {
        final (o, v) = project(r[2 * i], r[2 * i + 1]);
        pts[i] = o;
        ringVisible |= v;
      }
      if (!ringVisible) continue;
      any = true;
      path.addPolygon(pts, true);
    }
    return any ? path : null;
  }

  /// A filled band of the sphere between two latitudes, built from small cells so it survives the horizon.
  Path band(double latFrom, double latTo, {double latStep = 3, double lonStep = 6}) {
    final path = Path();
    final lo = math.min(latFrom, latTo), hi = math.max(latFrom, latTo);
    for (var la = lo; la < hi; la += latStep) {
      final la2 = math.min(hi, la + latStep);
      for (var lo2 = -180.0; lo2 < 180; lo2 += lonStep) {
        final a = project(lo2, la), b = project(lo2 + lonStep, la), c = project(lo2 + lonStep, la2), d = project(lo2, la2);
        if (!(a.$2 || b.$2 || c.$2 || d.$2)) continue;
        path.addPolygon([a.$1, b.$1, c.$1, d.$1], true);
      }
    }
    return path;
  }
}

double normalizeLon(double lon) {
  var l = (lon + 180) % 360;
  if (l < 0) l += 360;
  return l - 180;
}

// ---------------------------------------------------------------------------------------------
// Seasons. The globe is repainted for the selected month: snow cover, sea ice and vegetation
// follow the hemisphere's season (e.g. snow over much of North America in January).

/// Approximate southern edge of continuous snow cover in the northern hemisphere, by month (1-12).
const List<double> _snowLineNorth = [38, 39, 45, 55, 63, 70, 76, 76, 68, 58, 48, 41];

/// Approximate edge of Arctic sea ice by month (maximum in March, minimum in September).
const List<double> _seaIceNorth = [66, 64, 63, 66, 70, 74, 78, 80, 81, 77, 72, 68];

/// Approximate edge of Antarctic sea ice by month (maximum in September, minimum in February).
const List<double> _seaIceSouth = [-68, -70, -69, -66, -63, -60, -58, -57, -56, -58, -62, -66];

double snowLineNorth(int month) => _snowLineNorth[(month - 1) % 12];

/// Southern snow line mirrors the north six months later, a few degrees closer to the pole
/// because southern land is milder and more maritime.
double snowLineSouth(int month) => -(_snowLineNorth[(month + 5) % 12] + 4);

double seaIceNorth(int month) => _seaIceNorth[(month - 1) % 12];
double seaIceSouth(int month) => _seaIceSouth[(month - 1) % 12];

enum Season { winter, spring, summer, autumn }

Season seasonAt(double lat, int month) {
  final m = lat >= 0 ? month : ((month + 5) % 12) + 1; // southern hemisphere is six months offset
  if (m == 12 || m <= 2) return Season.winter;
  if (m <= 5) return Season.spring;
  if (m <= 8) return Season.summer;
  return Season.autumn;
}

const Color _tropical = Color(0xFF3F8F45);
const Map<Season, Color> _temperate = {
  Season.winter: Color(0xFF8E8B76),
  Season.spring: Color(0xFF79C46A),
  Season.summer: Color(0xFF4E9B3F),
  Season.autumn: Color(0xFFC98638),
};
const Color iceColor = Color(0xFFF3F7FB);
const Set<String> _iceSheets = {'AQ', 'GL'};

/// Land colour for a country in the selected month, based on its label latitude.
Color landColor(Country c, int month) {
  if (_iceSheets.contains(c.code)) return iceColor;
  final t = ((c.labelLat.abs() - 18) / 12).clamp(0.0, 1.0);
  return Color.lerp(_tropical, _temperate[seasonAt(c.labelLat, month)]!, t)!;
}

const List<String> monthNames = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

/// Short description of what the globe is showing for a month, for screen readers and the legend.
String seasonCaption(int month) {
  final north = seasonAt(45, month).name, south = seasonAt(-45, month).name;
  return '${monthNames[month - 1]}: $north in the north, $south in the south';
}
