// Configuration object with environment-specific settings
const CONFIG = {
  API_URL: 'https://raj-azure-resume-counter-apim.azure-api.net/raj-azure-resume-counter/counter',
  ELEMENT_ID: 'visitor-count',
  MAX_RETRIES: 3,
  RETRY_DELAY: 1000,
  TIMEOUT: 15000,
  DEBUG: true // Enable debug logging
};

class VisitorCounter {
  constructor(config) {
    this.config = config;
    this.controller = new AbortController();
    this.debugLog('Initializing VisitorCounter');
    this.initializeEventListeners();
  }

  debugLog(message, error = null) {
    if (this.config.DEBUG) {
      const timestamp = new Date().toISOString();
      console.log(`[VisitorCounter ${timestamp}] ${message}`);
      if (error) {
        console.error(`[VisitorCounter Error] ${error.message}`, error);
      }
    }
  }

  initializeEventListeners() {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => this.initialize());
    } else {
      // DOM already loaded, initialize immediately
      this.initialize();
    }

    // Cleanup on page unload
    window.addEventListener('unload', () => {
      this.debugLog('Cleaning up resources');
      this.controller.abort();
    });
  }

  async initialize() {
    this.debugLog('Starting initialization');
    try {
      // Validate API URL before making requests
      if (!this.isValidUrl(this.config.API_URL)) {
        throw new Error('Invalid API URL configuration');
      }
      const visitorCountElement = document.getElementById(this.config.ELEMENT_ID);

      // Initialize with loading state
      visitorCountElement.innerHTML = '<span id="count-value">Loading...</span></span>';


      await this.incrementVisitorCount();
      await this.updateVisitorCount();
      this.debugLog('Initialization completed successfully');
    } catch (error) {
      this.handleError('Failed to initialize visitor counter', error);
    }
  }

  isValidUrl(string) {
    try {
      const url = new URL(string);
      return url.protocol === 'http:' || url.protocol === 'https:';
    } catch {
      return false;
    }
  }

  async fetchWithRetryAndTimeout(url, options) {
    const { MAX_RETRIES, TIMEOUT, RETRY_DELAY } = this.config;

    for (let i = 0; i < MAX_RETRIES; i++) {
      this.debugLog(`Attempt ${i + 1} of ${MAX_RETRIES}`);

      const timeoutId = setTimeout(() => {
        this.controller.abort();
      }, TIMEOUT);

      try {
        const response = await fetch(url, {
          ...options,
          signal: this.controller.signal
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const contentType = response.headers.get('content-type');
        if (!contentType || !contentType.includes('application/json')) {
          throw new Error('Invalid response content-type');
        }

        return response;
      } catch (error) {
        clearTimeout(timeoutId);
        this.debugLog(`Fetch attempt ${i + 1} failed`, error);

        if (error.name === 'AbortError') {
          throw new Error('Request timed out');
        }

        if (i === MAX_RETRIES - 1) {
          throw error;
        }

        const delay = RETRY_DELAY * Math.pow(2, i); // Exponential backoff
        this.debugLog(`Waiting ${delay}ms before retry`);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
  }

  async makeRequest(method) {
    this.debugLog(`Making ${method} request to ${this.config.API_URL}`);

    try {
      const options = {
        method,
        headers: {
          'Accept': 'application/json',
          'Cache-Control': 'no-cache',
          'X-Correlation-ID': crypto.randomUUID()
        },
        credentials: 'same-origin',
        mode: 'cors'
      };

      if (method === 'POST') {
        options.headers['Content-Type'] = 'application/json';
        // Add CSRF token if available
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content;
        if (csrfToken) {
          options.headers['X-CSRF-Token'] = csrfToken;
        }
      }

      const response = await this.fetchWithRetryAndTimeout(this.config.API_URL, options);
      const data = await response.json();

      this.debugLog('Response data received', data);

      const count = data.count;
      if (!this.isValidVisitorCount(count)) {
        throw new Error(`Invalid visitor count received: ${count}`);
      }

      return count;
    } catch (error) {
      this.debugLog('Request failed', error);
      throw error;
    }
  }

  async updateVisitorCount() {
    try {
      const count = await this.makeRequest('GET');
      this.updateDOM(count);
      this.debugLog(`Visitor count updated: ${count}`);
    } catch (error) {
      this.handleError('Error fetching visitor count', error);
      throw error;
    }
  }

  async incrementVisitorCount() {
    try {
      const count = await this.makeRequest('POST');
      this.updateDOM(count);
      this.debugLog(`Visitor count incremented: ${count}`);
      return count;
    } catch (error) {
      this.handleError('Error incrementing visitor count', error);
      throw error;
    }
  }

  isValidVisitorCount(count) {
    if (typeof count !== 'number') {
      return false;
    }
    const parsed = parseInt(count, 10);
    return !isNaN(parsed) && parsed >= 0 && parsed <= Number.MAX_SAFE_INTEGER;
  }

  updateDOM(count) {
    localStorage.setItem('visitorCount', count.toString().replace(/[^\d]/g, ''));
    // Get references to the inner elements
    const countValueElement = document.getElementById('count-value');
    if (countValueElement) {
      // Sanitize the count before insertion
      const cachedCount = localStorage.getItem('visitorCount');
      countValueElement.textContent = cachedCount; // Use textContent instead of innerText for better security
      countValueElement.setAttribute('aria-label', `Visitor count: ${cachedCount}`);
      this.debugLog(`DOM updated with count: ${cachedCount}`);
    } else {
      this.debugLog(`Element with id ${this.config.ELEMENT_ID} not found`);
    }
  }

  handleError(message, error = null) {
    this.debugLog(message, error);

    const element = document.getElementById(this.config.ELEMENT_ID);
    if (element) {
      element.textContent = 'Unable to load visitor count';
      element.classList.add('counter-error');
      element.setAttribute('aria-label', 'Error loading visitor count');
    }
  }
}

// Initialize the counter
const visitorCounter = new VisitorCounter(CONFIG);

// Export for testing
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { VisitorCounter, CONFIG };
}
