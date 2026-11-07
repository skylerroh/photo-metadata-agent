// Poll lightweight status only; annotations are never overwritten while editing.
const review = document.querySelector('[data-job-id]');
if (review && ['queued', 'running', 'queued_review'].includes(review.dataset.status)) {
  const poll = async () => {
    try {
      const response = await fetch(`/api/jobs/${review.dataset.jobId}`);
      if (response.ok && (await response.json()).status !== review.dataset.status) {
        window.location.reload();
        return;
      }
    } catch { /* Keep the page usable while the local server restarts. */ }
    window.setTimeout(poll, 2000);
  };
  window.setTimeout(poll, 2000);
}
