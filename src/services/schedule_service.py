from datetime import datetime, timedelta
import pytz

class ScheduleService:
    def __init__(self):
        self.study_blocks = []

    def add_study_block(self, course_name, duration, urgency):
        block = {
            'course_name': course_name,
            'duration': duration,
            'urgency': urgency,
            'scheduled_time': None
        }
        self.study_blocks.append(block)

    def schedule_study_blocks(self):
        self.study_blocks.sort(key=lambda x: x['urgency'], reverse=True)
        current_time = datetime.now(pytz.utc)

        for block in self.study_blocks:
            if block['scheduled_time'] is None:
                block['scheduled_time'] = current_time
                current_time += timedelta(hours=block['duration'])

    def get_schedule(self):
        return [
            {
                'course_name': block['course_name'],
                'scheduled_time': block['scheduled_time'].strftime('%Y-%m-%d %H:%M:%S'),
                'duration': block['duration']
            }
            for block in self.study_blocks
        ]

    def clear_schedule(self):
        self.study_blocks = []