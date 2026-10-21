import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:my_news/models.dart';
import 'package:my_news/screens/ai_screen.dart';
import 'package:my_news/screens/perspective_screen.dart';

Map<String, dynamic> source(String id, String pub) =>
    {'article_id': id, 'publisher_name': pub, 'title': 'Article $id', 'url': 'https://example.com/$id'};

StoryDetail detail(Map<String, dynamic> ai) => StoryDetail.fromJson({
      'id': 'st_1',
      'headline': 'Original headline from the publisher',
      'articles': [],
      'ai': ai,
    });

Future<void> pump(WidgetTester tester, StoryDetail d) =>
    tester.pumpWidget(MaterialApp(home: Navigator(onGenerateRoute: (_) => MaterialPageRoute(builder: (_) => AiScreen(detail: d)))));

void main() {
  final okAi = {
    'status': 'ok',
    'label': 'AI-generated',
    'generated_at': '2026-10-20T10:05:00Z',
    'disclosure': 'Written by AI from RSS feed excerpts only.',
    'overview': 'Overview text.',
    'agreement': 'Agreement text.',
    'disagreement_or_uncertainty': 'Uncertainty text.',
    'overview_sources': [source('a1', 'BBC News')],
  };

  testWidgets('shows the fixed message when perspectives are not supported', (tester) async {
    await pump(tester, detail({...okAi, 'perspectives_status': 'insufficient', 'perspectives': []}));
    expect(find.textContaining('AI-generated'), findsWidgets);
    expect(find.text('Overview text.'), findsOneWidget);
    expect(find.text('Where sources agree'), findsOneWidget);
    expect(find.text('Disagreement or uncertainty'), findsOneWidget);
    expect(find.text('Written by AI from RSS feed excerpts only.'), findsOneWidget);
    expect(find.text(insufficientPerspectivesText), findsOneWidget);
  });

  testWidgets('a single perspective is not presented as a comparison', (tester) async {
    await pump(tester, detail({
      ...okAi,
      'perspectives_status': 'supported',
      'perspectives': [
        {'label': 'Only one', 'summary': 'S', 'sources': [source('a1', 'BBC News')]},
      ],
    }));
    expect(find.text('Only one'), findsNothing);
    expect(find.text(insufficientPerspectivesText), findsOneWidget);
  });

  testWidgets('perspectives open a detail view and back returns', (tester) async {
    await pump(tester, detail({
      ...okAi,
      'perspectives_status': 'supported',
      'perspectives': [
        {'label': 'Economic angle', 'summary': 'Costs rise.', 'sources': [source('a1', 'BBC News')]},
        {'label': 'Safety angle', 'summary': 'Risks fall.', 'sources': [source('a2', 'NPR')]},
      ],
    }));
    expect(find.text(insufficientPerspectivesText), findsNothing);
    await tester.ensureVisible(find.text('Safety angle'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Safety angle'));
    await tester.pumpAndSettle();
    expect(find.byType(PerspectiveScreen), findsOneWidget);
    expect(find.text('Risks fall.'), findsOneWidget);

    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.byType(PerspectiveScreen), findsNothing);
    expect(find.text('Economic angle'), findsOneWidget);
  });

  testWidgets('a failed AI call is reported, never filled in', (tester) async {
    await pump(tester, detail({'status': 'failed', 'message': 'The AI service did not respond.'}));
    expect(find.text('AI overview unavailable'), findsOneWidget);
    expect(find.text('The AI service did not respond.'), findsOneWidget);
    expect(find.text('Overview'), findsNothing);
  });
}
