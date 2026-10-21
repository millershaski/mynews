import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import 'models.dart';

/// Base URL of the My News API, injected at build time:
///   flutter build apk --dart-define=API_BASE_URL=https://my-news-api.onrender.com
/// The app never holds any API keys; every credential lives on the server.
const String apiBaseUrl = String.fromEnvironment('API_BASE_URL');

class ApiException implements Exception {
  final String message;

  /// True when the device could not reach the server at all (offline, DNS, timeout).
  final bool network;
  final int? statusCode;

  const ApiException(this.message, {this.network = false, this.statusCode});

  @override
  String toString() => message;
}

class ApiClient {
  final String baseUrl;
  final http.Client _http;

  ApiClient({String? baseUrl, http.Client? client})
      : baseUrl = (baseUrl ?? apiBaseUrl).replaceAll(RegExp(r'/+$'), ''),
        _http = client ?? http.Client();

  bool get configured => baseUrl.isNotEmpty;

  Future<Map<String, dynamic>> getJson(
    String path, {
    Map<String, String>? query,
    Duration timeout = const Duration(seconds: 60), // free hosting can take ~a minute to wake up
  }) async {
    if (!configured) {
      throw const ApiException(
        'This build has no server address. Rebuild with --dart-define=API_BASE_URL=<your API URL>.',
      );
    }
    final uri = Uri.parse('$baseUrl$path').replace(queryParameters: query);
    http.Response res;
    try {
      res = await _http.get(uri, headers: {'Accept': 'application/json'}).timeout(timeout);
    } on TimeoutException {
      throw const ApiException('The server took too long to answer. Check your connection and try again.',
          network: true);
    } on SocketException {
      throw const ApiException('Could not reach the server. You may be offline.', network: true);
    } on http.ClientException catch (e) {
      throw ApiException('Could not reach the server (${e.message}).', network: true);
    }
    Object? body;
    try {
      body = jsonDecode(utf8.decode(res.bodyBytes));
    } on FormatException {
      body = null;
    }
    if (res.statusCode != 200) {
      final detail = body is Map && body['detail'] is String ? body['detail'] as String : null;
      throw ApiException(detail ?? 'The server answered with an error (HTTP ${res.statusCode}).',
          statusCode: res.statusCode);
    }
    if (body is! Map) {
      throw const ApiException('The server sent a response the app could not read.');
    }
    return body.cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> storiesJson({int limit = 100}) =>
      getJson('/stories', query: {'limit': '$limit'});

  Future<Map<String, dynamic>> storyJson(String id) => getJson('/stories/${Uri.encodeComponent(id)}');

  Future<ExploreResult> explore({
    required String country,
    required int year,
    required int month,
    required String category,
  }) async {
    final j = await getJson(
      '/explore',
      query: {'country': country, 'year': '$year', 'month': '$month', 'category': category},
      // The first request for a country/month runs an archive search plus an AI call, which may fall back
      // to a second model when the first is busy (each call can take up to 90 s on the free tier).
      timeout: const Duration(seconds: 180),
    );
    return ExploreResult.fromJson(j);
  }

  Future<Map<String, dynamic>> status() => getJson('/status');
}
