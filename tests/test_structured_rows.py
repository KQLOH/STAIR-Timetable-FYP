"""Synthetic portal structure regression; not actual Week 3 event labels."""
import unittest
from backend.services.timetable_parser import parse_intranet_response
from backend.services.event_adapter import adapt_tarumt_response,canonical_api_data
from tests.fixtures import timetable_html

def html_fixture():
    html=timetable_html(header='Mon 2033-02-07')
    # A separate end-time header row is a normal two-row timetable axis.
    header='<tr><td></td>'+''.join(f'<td>{hour:02}:00</td>' for hour in range(9,22))+'</tr>'
    html=html.replace('</tr>', '</tr>'+header,1)
    for i in range(1,6):
        start,end=i+9,i+10
        a=f"{start%12 or 12}:00 {'AM' if start<12 else 'PM'}";b=f"{end%12 or 12}:00 {'AM' if end<12 else 'PM'}"
        venue=('ZX204' if i==4 else 'QW305' if i==5 else 'SYNTHETIC VENUE')
        html=html.replace('</table>',f'<tr><td>Tue 2033-02-08</td><td style="background-color:#46854d"><div data-rel="tooltip" title="SYNTHETIC COURSE (Lecture) SYNTHETIC PERSON ({venue}, Synthetic Block)">TEST{1100+i} (L)<br>{venue}<br>SYNTHETIC PERSON<br>{a} - {b}</div></td></tr></table>')
    return html

def details_found(packet):
    return sum(bool((e.revision.content.course_code or e.revision.content.course_name) and
        (e.revision.content.date or e.revision.content.day_of_week) and
        all(getattr(e.revision.content,k) is not None for k in ('session_type','start_time','end_time','location')) and
        all(o.state.value not in ('UNRESOLVED','CONFLICTING') for o in e.revision.observations)) for e in packet.events)

class StructuredRowsTests(unittest.TestCase):
    def test_two_row_axis_and_course_shaped_rooms_do_not_create_phantom_classes(self):
        parsed=parse_intranet_response(html_fixture());packet=adapt_tarumt_response(parsed)
        self.assertEqual(len(parsed['events']),6);self.assertEqual(len(packet.events),6);self.assertEqual(details_found(packet),6)
        self.assertEqual(len({e.candidate_id for e in packet.events}),6)
        data=canonical_api_data(parsed);self.assertEqual(len(data['application']['policy']),6)
        self.assertTrue(all(p['decision']=='REVIEW' for p in data['application']['policy']))
        self.assertTrue(all(not p['sandbox_write_permitted'] for p in data['application']['calendar']['eligibility']['events']))
    def test_time_header_is_retained_as_source_evidence_not_event(self):
        packet=adapt_tarumt_response(parse_intranet_response(html_fixture()))
        headers=[s for s in packet.evidence if 'non_session:time_axis_header' in s.transformations]
        self.assertEqual(len(headers),1);self.assertIn('09:00',headers[0].raw_text);self.assertIsNotNone(headers[0].raw_html)
        self.assertTrue(all(headers[0].evidence_id not in o.evidence_ids for e in packet.events for o in e.revision.observations))
    def test_venue_identifiers_remain_venues_and_source_course_markers_remain_courses(self):
        packet=adapt_tarumt_response(parse_intranet_response(html_fixture()))
        for e in packet.events:
            c=e.revision.content;self.assertIsNotNone(c.course_code)
            self.assertTrue(c.course_code.startswith('TEST'))
            if c.location in {'ZX204','QW305'}:
                self.assertNotEqual(c.course_code,c.location)
                self.assertEqual(next(o.state.value for o in e.revision.observations if o.field_name=='course_code'),'PRESENT')
    def test_malformed_class_row_is_not_silently_removed_as_header(self):
        html=timetable_html().replace('</table>','<tr><td>Unknown day</td><td><div data-rel="tooltip">TEST2002 (T) 11:00 AM - 12:00 PM</div></td></tr></table>')
        packet=adapt_tarumt_response(parse_intranet_response(html));self.assertEqual(len(packet.events),2)
        self.assertTrue(any(i.code=='UNPARSED_ROW_HEADER' for i in packet.issues))
    def test_genuine_multiple_course_interpretations_remain_unresolved(self):
        packet=adapt_tarumt_response(parse_intranet_response(timetable_html(visible='TEST1001 / TEST2002 (L) 9:00 AM - 10:00 AM')))
        obs=next(o for o in packet.events[0].revision.observations if o.field_name=='course_code')
        self.assertEqual(obs.state.value,'UNRESOLVED');self.assertEqual(len(obs.alternatives),2)
    def test_nested_presentational_rows_are_not_new_sessions(self):
        html=timetable_html(visible='TEST1001 (L)<table><tr><td>Room</td><td>ZX204</td></tr></table>9:00 AM - 10:00 AM')
        packet=adapt_tarumt_response(parse_intranet_response(html));self.assertEqual(len(packet.events),1)
        self.assertEqual(packet.events[0].revision.content.course_code,'TEST1001')

    def test_known_previous_adapter_revision_remains_readable_without_rewriting(self):
        from backend.services.event_adapter import LEGACY_CONFIGURATION_DIGEST
        from backend.review.structured import structured_application
        from backend.review.binding import AssessmentInput,bind_input
        from backend.models.event import CanonicalTimetable
        packet=adapt_tarumt_response(parse_intranet_response(timetable_html()))
        events=tuple(e.model_copy(update={'revision':e.revision.model_copy(update={'configuration_digest':LEGACY_CONFIGURATION_DIGEST})}) for e in packet.events)
        previous=packet.model_copy(update={'events':events});app=structured_application(previous)
        run=bind_input(AssessmentInput(source_packet=previous,reconstruction=app['reconstruction'],policy_profile='m5-source-review-v1'))
        self.assertEqual(run.events()[0].revision,previous.events[0].revision)
        self.assertEqual(run.graph().settings_digest,LEGACY_CONFIGURATION_DIGEST)
        self.assertFalse(app['policy'][0]['registered_routing_eligible'])

    def test_name_only_session_never_promotes_body_venue_to_course(self):
        html=timetable_html(visible='SYNTHETIC COURSE (L)<br>ZX204<br>9:00 AM - 10:00 AM')
        packet=adapt_tarumt_response(parse_intranet_response(html));event=packet.events[0]
        self.assertIsNone(event.revision.content.course_code)
        self.assertIsNotNone(event.revision.content.course_name)
        self.assertNotEqual(event.revision.content.course_name,'ZX204')
