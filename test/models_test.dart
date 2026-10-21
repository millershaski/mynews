import 'package:flutter_test/flutter_test.dart';
import 'package:my_news/models.dart';

Map<String, dynamic> storyJson() => {
      'id': 'st_1',
      'headline': 'Storm hits coast',
      'latest_published_at': '2026-10-20T10:00:00Z',
      'publisher_count': 2,
      'publishers': ['BBC News', 'NPR'],
      'article_count': 3,
      'ai_status': 'ok',
      'articles': [
        {
          'id': 'a1',
          'title': 'Storm hits coast',
          'publisher_id': 'bbc',
          'publisher_name': 'BBC News',
          'url': 'https://bbc.example/1',
          'excerpt': 'Winds of 100mph.',
          'published_at': '2026-10-20T10:00:00Z',
          'date_estimated': false,
        },
        {
          'id': 'a2',
          'title': 'Storm live updates',
          'publisher_id': 'npr',
          'publisher_name': 'NPR',
          'url': 'https://npr.example/2',
          'excerpt': '',
          'published_at': '2026-10-20T09:00:00Z',
          'date_estimated': true,
        },
        {
          'id': 'a3',
          'title': 'Storm aftermath',
          'publisher_id': 'bbc',
          'publisher_name': 'BBC News',
          'url': 'https://bbc.example/3',
          'excerpt': '',
          'published_at': '2026-10-19T09:00:00Z',
        },
      ],
      'background': {
        'status': 'ok',
        'resources': [
          {'title': 'Tropical cyclone', 'url': 'https://en.wikipedia.org/wiki/Tropical_cyclone', 'source': 'Wikipedia'},
        ],
      },
      'ai': {
        'status': 'ok',
        'label': 'AI-generated',
        'disclosure': 'Based only on RSS excerpts.',
        'generated_at': '2026-10-20T10:05:00Z',
        'model': 'gemini-2.5-flash',
        'prompt_version': 'v1',
        'overview': 'A storm reached the coast.',
        'agreement': 'Both report high winds.',
        'disagreement_or_uncertainty': 'Damage totals unclear.',
        'overview_sources': [
          {'article_id': 'a1', 'publisher_name': 'BBC News', 'title': 'Storm hits coast', 'url': 'https://bbc.example/1'},
        ],
        'perspectives_status': 'insufficient',
        'perspectives': [],
        'perspectives_message': 'Insufficient evidence for distinct perspectives',
      },
      'last_successful_collection_at': '2026-10-20T11:00:00Z',
    };

void main() {
  group('parseTime', () {
    test('parses ISO strings to UTC', () {
      final t = parseTime('2026-10-20T12:00:00+02:00');
      expect(t, DateTime.utc(2026, 10, 20, 10));
      expect(t!.isUtc, isTrue);
    });

    test('returns null for missing or invalid values', () {
      expect(parseTime(null), isNull);
      expect(parseTime(''), isNull);
      expect(parseTime('not a date'), isNull);
      expect(parseTime(42), isNull);
    });
  });

  group('StoryDetail', () {
    test('parses articles, background and AI', () {
      final d = StoryDetail.fromJson(storyJson());
      expect(d.summary.id, 'st_1');
      expect(d.summary.publisherCount, 2);
      expect(d.articles, hasLength(3));
      expect(d.articles[1].dateEstimated, isTrue);
      expect(d.articles[2].dateEstimated, isFalse);
      expect(d.backgroundStatus, 'ok');
      expect(d.background.single.label, 'Tropical cyclone'); // label falls back to title
      expect(d.ai.hasText, isTrue);
      expect(d.ai.overviewSources.single.articleId, 'a1');
      expect(d.ai.perspectives, isEmpty);
      expect(d.lastSuccessfulCollectionAt, DateTime.utc(2026, 10, 20, 11));
    });

    test('groups articles by publisher, keeping order', () {
      final byPub = StoryDetail.fromJson(storyJson()).articlesByPublisher;
      expect(byPub.keys, ['BBC News', 'NPR']);
      expect(byPub['BBC News']!.map((a) => a.id), ['a1', 'a3']);
    });

    test('tolerates missing background and AI blocks', () {
      final d = StoryDetail.fromJson({'id': 'x', 'headline': 'H'});
      expect(d.articles, isEmpty);
      expect(d.background, isEmpty);
      expect(d.ai.status, 'pending');
      expect(d.ai.label, 'AI-generated');
      expect(d.ai.hasText, isFalse);
    });
  });

  group('StoryAi.hasText', () {
    StoryAi ai(String status, String? overview) =>
        StoryAi.fromJson({'status': status, 'overview': ?overview});

    test('shows text only for ok or updating with an overview', () {
      expect(ai('ok', 'text').hasText, isTrue);
      expect(ai('updating', 'text').hasText, isTrue);
      expect(ai('ok', null).hasText, isFalse);
      expect(ai('ok', '').hasText, isFalse);
      expect(ai('failed', 'text').hasText, isFalse);
      expect(ai('outdated', 'text').hasText, isFalse);
      expect(ai('pending', 'text').hasText, isFalse);
    });
  });

  group('StoryList', () {
    test('defaults the window to 168 hours', () {
      final l = StoryList.fromJson({'stories': [], 'total': 0});
      expect(l.windowHours, 168);
      expect(l.stories, isEmpty);
    });
  });

  group('ExploreResult', () {
    test('parses a pick with AI reasoning', () {
      final r = ExploreResult.fromJson({
        'country': {'code': 'JP', 'name': 'Japan'},
        'year': 2024,
        'month': 1,
        'month_label': 'January 2024',
        'category': {'id': 'science', 'label': 'Science'},
        'is_current_month': false,
        'status': 'ok',
        'pick': {
          'headline': 'Moon lander touches down',
          'trail_text': 'SLIM lands.',
          'url': 'https://www.bbc.co.uk/news/x',
          'publisher': 'BBC News',
          'published_at': '2024-01-20T00:00:00Z',
        },
        'ai': {'reason': 'Major science milestone.', 'generated_at': '2026-10-20T00:00:00Z'},
        'cached': true,
        'sources': [
          {'publisher': 'BBC News', 'via': 'Wayback Machine RSS archive', 'status': 'ok', 'candidates': 3},
          {
            'publisher': 'The Guardian',
            'via': 'Guardian Open Platform archive search',
            'status': 'not_configured',
            'detail': 'GUARDIAN_API_KEY is not set',
            'candidates': 0,
          },
        ],
        'live_stories': [],
      });
      expect(r.countryName, 'Japan');
      expect(r.categoryId, 'science');
      expect(r.pick!.publisher, 'BBC News');
      expect(r.ai!.label, 'AI-generated');
      expect(r.cached, isTrue);
      expect(r.canRetry, isFalse);
      expect(r.sources.map((s) => s.publisher), ['BBC News', 'The Guardian']);
      expect(r.sources.first.candidates, 3);
      expect(r.sources.last.status, 'not_configured');
      expect(r.sources.last.detail, 'GUARDIAN_API_KEY is not set');
    });

    test('a pick without a publisher is not attributed to one', () {
      final r = ExploreResult.fromJson({
        'status': 'ok',
        'pick': {'headline': 'h', 'url': 'https://example.com/x'},
      });
      expect(r.pick!.publisher, 'Unknown publisher');
      expect(r.sources, isEmpty);
    });

    test('missing status is treated as a retryable source failure', () {
      final r = ExploreResult.fromJson({});
      expect(r.status, 'source_unavailable');
      expect(r.categoryId, 'all');
      expect(r.pick, isNull);
      expect(r.canRetry, isTrue);
    });

    test('only temporary failures can be retried', () {
      for (final (status, retry) in [
        ('ok', false),
        ('no_match', false),
        ('no_candidates', false),
        ('relevance_fallback', true),
        ('ai_unavailable', true),
        ('source_unavailable', true),
      ]) {
        expect(ExploreResult.fromJson({'status': status}).canRetry, retry, reason: status);
      }
    });
  });
}
