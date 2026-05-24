from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='selfpaced.Course')
def recompute_pod_paces_on_new_course(sender, instance, created, **kwargs):
    """
    When a new course is added to a programme, recompute pace for every active
    pod in that programme — course count affects total_courses and therefore
    required_pace for all assigned learners.
    """
    if not created:
        return

    from selfpaced.models import Pod
    from selfpaced.pace import compute_pod_paces

    for pod in Pod.objects.filter(
        programme=instance.programme,
        status='active',
    ).prefetch_related('assignments__learner', 'assignments__programme'):
        compute_pod_paces(pod)
