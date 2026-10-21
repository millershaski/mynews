import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:my_news/api.dart';
import 'package:my_news/models.dart';
import 'package:my_news/repository.dart';
import 'package:shared_preferences/shared_preferences.dart';

final now = DateTime.utc(2026, 10, 21, 12);
final cutoff = now.subtract(retentionWindow);

StorySummary summary(String id, DateTime? latest) => StorySummary(
      id: id,
      headline: 'Story $id',
      latestPublishedAt: latest,
      publisherCount: 1,
      publishers: const ['BBC News'],
      articleCount: 1,
      aiStatus: 'ok',
    );

Map<String, dynamic> summaryJson(String id, DateTime latest) => {
      'id': id,
      'headline': 'Story $id',
      'latest_published_at': latest.toIso8601String(),
      'publisher_count': 1,
      'publishers': ['BBC News'],
      'article_count': 1,
      'ai_status': 'ok',
    };

Map<String, dynamic> detailJson(List<DateTime> articleTimes) => {
      'id': 'st_1',
      'headline': 'Story',
      'latest_published_at': articleTimes.first.toIso8601String(),
      'publisher_count': 2,
      'publishers': ['BBC News', 'NPR'],
      'article_count': articleTimes.length,
      'ai_status': 'ok',
      'articles': [
        for (var i = 0; i < articleTimes.length; i++)
          {
            'id': 'a$i',
            'title': 'Article $i',
            'publisher_name': i.isEven ? 'BBC News' : 'NPR',
            'url': 'https://example.com/$i',
            'published_at': articleTimes[i].toIso8601String(),
          }
      ],
      'ai': {'status': 'ok', 'overview': 'Overview', 'generated_at': now.toIso8601String()},
    };

/// A repository whose HTTP layer is driven by [handler].
NewsRepository repo(Future<http.Response> Function(http.Request) handler) => NewsRepository(
      api: ApiClient(baseUrl: 'https://api.test', client: MockClient(handler)),
      prefs: SharedPreferences.getInstance(),
      now: () => now,
    );

Future<http.Response> offline(http.Request _) async => throw const SocketException('offline');

void main() {
  group('filterExpired', () {
    test('drops stories outside the 7-day window and sorts newest first', () {
      final list = StoryList(
        stories: [
          summary('old', now.subtract(const Duration(hours: 169))),
          summary('mid', now.subtract(const Duration(hours: 20))),
          summary('none', null),
          summary('new', now.subtract(const Duration(hours: 1))),
          summary('edge', cutoff),
        ],
        total: 5,
        windowHours: 168,
        lastSuccessfulCollectionAt: null,
        generatedAt: null,
      );
      final out = filterExpired(list, cutoff);
      expect(out.stories.map((s) => s.id), ['new', 'mid', 'edge']);
      expect(out.total, 3);
    });
  });

  group('filterExpiredDetail', () {
    test('returns the same detail when nothing expired', () {
      final d = StoryDetail.fromJson(detailJson([now.subtract(const Duration(hours: 2))]));
      expect(identical(filterExpiredDetail(d, cutoff), d), isTrue);
    });

    test('returns null when every article expired', () {
      final d = StoryDetail.fromJson(detailJson([now.subtract(const Duration(days: 8))]));
      expect(filterExpiredDetail(d, cutoff), isNull);
    });

    test('removes expired articles and hides AI text that may depend on them', () {
      final d = StoryDetail.fromJson(detailJson([
        now.subtract(const Duration(hours: 2)),
        now.subtract(const Duration(days: 9)),
      ]));
      expect(d.ai.hasText, isTrue);
      final out = filterExpiredDetail(d, cutoff)!;
      expect(out.articles.map((a) => a.id), ['a0']);
      expect(out.summary.publisherCount, 1);
      expect(out.summary.publishers, ['BBC News']);
      expect(out.ai.status, 'outdated');
      expect(out.ai.overview, isNull);
      expect(out.ai.hasText, isFalse);
    });
  });

  group('filterExpiredExplore', () {
    test('keeps the archive pick but drops expired live stories', () {
      final r = ExploreResult.fromJson({
        'status': 'ok',
        'pick': {'headline': 'Archive pick', 'url': 'https://x'},
        'live_stories': [
          summaryJson('fresh', now.subtract(const Duration(hours: 3))),
          summaryJson('stale', now.subtract(const Duration(days: 10))),
        ],
      });
      final out = filterExpiredExplore(r, cutoff);
      expect(out.pick!.headline, 'Archive pick');
      expect(out.liveStories.map((s) => s.id), ['fresh']);
    });
  });

  group('NewsRepository', () {
    setUp(() => SharedPreferences.setMockInitialValues({}));

    test('online fetch caches the list and records the sync time', () async {
      final r = repo((req) async => http.Response(
          jsonEncode({
            'stories': [summaryJson('a', now.subtract(const Duration(hours: 1)))],
            'total': 1,
            'window_hours': 168,
          }),
          200));
      final loaded = await r.stories();
      expect(loaded.offline, isFalse);
      expect(loaded.data.stories.single.id, 'a');
      expect(await r.lastSync(), now);
    });

    test('offline falls back to the cached list without expired stories', () async {
      SharedPreferences.setMockInitialValues({
        'cache.stories.v1': jsonEncode({
          'stories': [
            summaryJson('fresh', now.subtract(const Duration(hours: 5))),
            summaryJson('stale', now.subtract(const Duration(days: 8))),
          ],
          'total': 2,
        }),
        'cache.stories.synced_at': now.subtract(const Duration(hours: 4)).toIso8601String(),
      });
      final loaded = await repo(offline).stories();
      expect(loaded.offline, isTrue);
      expect(loaded.offlineReason, isNotNull);
      expect(loaded.syncedAt, now.subtract(const Duration(hours: 4)));
      expect(loaded.data.stories.map((s) => s.id), ['fresh']);
    });

    test('offline with no cache reports the network error', () async {
      expect(
        repo(offline).stories(),
        throwsA(isA<ApiException>().having((e) => e.network, 'network', isTrue)),
      );
    });

    test('server errors are not hidden behind the offline copy', () async {
      SharedPreferences.setMockInitialValues({
        'cache.stories.v1': jsonEncode({'stories': [], 'total': 0}),
      });
      final r = repo((req) async => http.Response(jsonEncode({'detail': 'Database unavailable'}), 503));
      expect(
        r.stories(),
        throwsA(isA<ApiException>().having((e) => e.statusCode, 'statusCode', 503)),
      );
    });

    test('offline story detail hides AI when some articles expired', () async {
      final json = detailJson([now.subtract(const Duration(hours: 1)), now.subtract(const Duration(days: 9))]);
      final online = repo((req) async => http.Response(jsonEncode(json), 200));
      await online.story('st_1');

      final loaded = await repo(offline).story('st_1');
      expect(loaded.offline, isTrue);
      expect(loaded.data.articles, hasLength(1));
      expect(loaded.data.ai.status, 'outdated');
    });

    test('offline story detail that fully expired is not shown', () async {
      final json = detailJson([now.subtract(const Duration(days: 9))]);
      await repo((req) async => http.Response(jsonEncode(json), 200)).story('st_1');
      expect(
        repo(offline).story('st_1'),
        throwsA(isA<ApiException>().having((e) => e.message, 'message', contains('older than 7 days'))),
      );
    });

    test('a 404 removes the cached story', () async {
      final json = detailJson([now.subtract(const Duration(hours: 1))]);
      await repo((req) async => http.Response(jsonEncode(json), 200)).story('st_1');
      await expectLater(
        repo((req) async => http.Response(jsonEncode({'detail': 'Story not found'}), 404)).story('st_1'),
        throwsA(isA<ApiException>()),
      );
      await expectLater(repo(offline).story('st_1'), throwsA(isA<ApiException>()));
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getKeys().where((k) => k.startsWith('cache.story.v1.')), isEmpty);
    });

    test('explore caches successful picks but not temporary failures', () async {
      Map<String, dynamic> body(String status) => {
            'country': {'code': 'FR', 'name': 'France'},
            'year': 2020,
            'month': 6,
            'category': {'id': 'all', 'label': 'All'},
            'status': status,
            if (status == 'ok') 'pick': {'headline': 'Pick', 'url': 'https://x', 'publisher': 'NPR'},
            'sources': [
              {'publisher': 'NPR', 'via': 'Wayback Machine RSS archive', 'status': 'ok', 'candidates': 4},
            ],
          };

      await repo((req) async => http.Response(jsonEncode(body('ai_unavailable')), 200))
          .explore(country: 'FR', year: 2020, month: 6, category: 'all');
      await expectLater(
        repo(offline).explore(country: 'FR', year: 2020, month: 6, category: 'all'),
        throwsA(isA<ApiException>()),
      );

      await repo((req) async => http.Response(jsonEncode(body('ok')), 200))
          .explore(country: 'FR', year: 2020, month: 6, category: 'all');
      final cached = await repo(offline).explore(country: 'FR', year: 2020, month: 6, category: 'all');
      expect(cached.offline, isTrue);
      expect(cached.data.status, 'ok');
      expect(cached.data.pick!.headline, 'Pick');
      expect(cached.data.countryName, 'France');
      expect(cached.data.pick!.publisher, 'NPR');
      expect(cached.data.sources.single.publisher, 'NPR');
      expect(cached.data.sources.single.candidates, 4);
    });

    test('requests carry the query parameters the API documents', () async {
      late Uri seen;
      await repo((req) async {
        seen = req.url;
        return http.Response(jsonEncode({'status': 'no_match'}), 200);
      }).explore(country: 'BR', year: 2019, month: 12, category: 'feel_good');
      expect(seen.path, '/explore');
      expect(seen.queryParameters, {'country': 'BR', 'year': '2019', 'month': '12', 'category': 'feel_good'});
    });
  });

  test('an unconfigured client explains how to set the server address', () async {
    expect(
      ApiClient(baseUrl: '').storiesJson(),
      throwsA(isA<ApiException>().having((e) => e.message, 'message', contains('API_BASE_URL'))),
    );
  });
}
