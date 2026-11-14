sandbox: accept 0 as the default limit

Callers that leave MaxSeconds unset were refused; zero now means the runner's default.
