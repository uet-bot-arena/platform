export const labReport = {
  "schema_version": 1,
  "report_id": "LAB-20261010-01",
  "origin": "internal_lab",
  "submission_id": null,
  "measured_at": "2026-10-10T03:48:19Z",
  "environment": {
    "cpu": "13th Gen Intel(R) Core(TM) i5-13420H",
    "platform": "Linux-6.18.40.1-microsoft-standard-WSL2-x86_64-with-glibc2.39",
    "python": "3.12.3 (main, Jun 19 2026, 12:46:00) [GCC 13.3.0]",
    "codon": "0.20.3",
    "g++": "g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
    "cpu_affinity": [
      1
    ]
  },
  "source_hashes": {
    "bot_python_sha256": "fc82682a53a31fcb8e35f569548ad3802624770cfe14fae768df04eb2f9ec1a6",
    "bot_cpp_sha256": "e949258a3b64141b90f7c47aceb773f835ad969aef369a136f181e3b6d12ff67",
    "compiler_sha256": "beb6fb28fff2a9444a12c06f54df5fd9c090a01673e96c0f2323782c2add07bf"
  },
  "suite_sha256": "fd897dcee88ca2534a9a3ef43bd42430714573142ced1884ff4efbbc0c6d325a",
  "method": {
    "game": "Synthetic 9x9 connect-five, not OTTv2",
    "single_threaded": true,
    "minimax_depth": 3,
    "branch_cap": 8,
    "mc_rollouts": 128,
    "mc_max_plies": 24,
    "mc_kind": "Flat Monte Carlo; no MCTS tree",
    "repeats": 5,
    "benchmark_positions": 24,
    "correctness_positions": 29,
    "timing": "compute excludes protocol parsing; roundtrip includes parse/serialize and parent pipe I/O; warmed caches",
    "source_location": "Windows filesystem mounted in WSL2; timing is exploratory, not production capacity"
  },
  "correctness": {
    "passed": true,
    "cross_runtime_cases": 87,
    "runtime_responses_checked": 261,
    "protocol_checks": {
      "cpython": true,
      "codon": true,
      "cpp": true
    },
    "cases": [
      {
        "fixture": "random-0",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 40,
        "score": 86,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-0",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 34,
        "score": 65,
        "nodes": 247,
        "all_equal": true
      },
      {
        "fixture": "random-0",
        "algorithm": "mc",
        "parameter": 128,
        "move": 40,
        "score": 10,
        "nodes": 3185,
        "all_equal": true
      },
      {
        "fixture": "random-1",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 40,
        "score": 83,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-1",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 40,
        "score": 149,
        "nodes": 119,
        "all_equal": true
      },
      {
        "fixture": "random-1",
        "algorithm": "mc",
        "parameter": 128,
        "move": 31,
        "score": 6,
        "nodes": 3178,
        "all_equal": true
      },
      {
        "fixture": "random-2",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 48,
        "score": 476,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-2",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 41,
        "score": 1642,
        "nodes": 233,
        "all_equal": true
      },
      {
        "fixture": "random-2",
        "algorithm": "mc",
        "parameter": 128,
        "move": 48,
        "score": 14,
        "nodes": 3067,
        "all_equal": true
      },
      {
        "fixture": "random-3",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 58,
        "score": -125,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-3",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 58,
        "score": -773,
        "nodes": 45,
        "all_equal": true
      },
      {
        "fixture": "random-3",
        "algorithm": "mc",
        "parameter": 128,
        "move": 61,
        "score": 2,
        "nodes": 2617,
        "all_equal": true
      },
      {
        "fixture": "random-4",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 40,
        "score": -56,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-4",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 43,
        "score": -158,
        "nodes": 260,
        "all_equal": true
      },
      {
        "fixture": "random-4",
        "algorithm": "mc",
        "parameter": 128,
        "move": 55,
        "score": -4,
        "nodes": 2415,
        "all_equal": true
      },
      {
        "fixture": "random-5",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 68,
        "score": 1000000,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-5",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 68,
        "score": 1000000,
        "nodes": 44,
        "all_equal": true
      },
      {
        "fixture": "random-5",
        "algorithm": "mc",
        "parameter": 128,
        "move": 68,
        "score": 16,
        "nodes": 526,
        "all_equal": true
      },
      {
        "fixture": "random-6",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 53,
        "score": 195,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-6",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 53,
        "score": 103,
        "nodes": 182,
        "all_equal": true
      },
      {
        "fixture": "random-6",
        "algorithm": "mc",
        "parameter": 128,
        "move": 39,
        "score": 2,
        "nodes": 3162,
        "all_equal": true
      },
      {
        "fixture": "random-7",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 68,
        "score": 155,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-7",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 68,
        "score": 140,
        "nodes": 259,
        "all_equal": true
      },
      {
        "fixture": "random-7",
        "algorithm": "mc",
        "parameter": 128,
        "move": 48,
        "score": 10,
        "nodes": 3149,
        "all_equal": true
      },
      {
        "fixture": "random-8",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 31,
        "score": 126,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-8",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 31,
        "score": 170,
        "nodes": 63,
        "all_equal": true
      },
      {
        "fixture": "random-8",
        "algorithm": "mc",
        "parameter": 128,
        "move": 39,
        "score": 8,
        "nodes": 2794,
        "all_equal": true
      },
      {
        "fixture": "random-9",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 22,
        "score": 800,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-9",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 68,
        "score": 1002,
        "nodes": 243,
        "all_equal": true
      },
      {
        "fixture": "random-9",
        "algorithm": "mc",
        "parameter": 128,
        "move": 40,
        "score": 8,
        "nodes": 2852,
        "all_equal": true
      },
      {
        "fixture": "random-10",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 31,
        "score": 1881,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-10",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 41,
        "score": 1765,
        "nodes": 172,
        "all_equal": true
      },
      {
        "fixture": "random-10",
        "algorithm": "mc",
        "parameter": 128,
        "move": 34,
        "score": 12,
        "nodes": 1997,
        "all_equal": true
      },
      {
        "fixture": "random-11",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 12,
        "score": 1000000,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-11",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 12,
        "score": 1000000,
        "nodes": 45,
        "all_equal": true
      },
      {
        "fixture": "random-11",
        "algorithm": "mc",
        "parameter": 128,
        "move": 12,
        "score": 16,
        "nodes": 650,
        "all_equal": true
      },
      {
        "fixture": "random-12",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 29,
        "score": 23,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-12",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 29,
        "score": 59,
        "nodes": 280,
        "all_equal": true
      },
      {
        "fixture": "random-12",
        "algorithm": "mc",
        "parameter": 128,
        "move": 28,
        "score": 8,
        "nodes": 3185,
        "all_equal": true
      },
      {
        "fixture": "random-13",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 38,
        "score": 31,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-13",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 38,
        "score": 67,
        "nodes": 304,
        "all_equal": true
      },
      {
        "fixture": "random-13",
        "algorithm": "mc",
        "parameter": 128,
        "move": 38,
        "score": 10,
        "nodes": 3161,
        "all_equal": true
      },
      {
        "fixture": "random-14",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 66,
        "score": -51,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-14",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 32,
        "score": -151,
        "nodes": 210,
        "all_equal": true
      },
      {
        "fixture": "random-14",
        "algorithm": "mc",
        "parameter": 128,
        "move": 32,
        "score": 4,
        "nodes": 3129,
        "all_equal": true
      },
      {
        "fixture": "random-15",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 67,
        "score": 648,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-15",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 49,
        "score": 88,
        "nodes": 134,
        "all_equal": true
      },
      {
        "fixture": "random-15",
        "algorithm": "mc",
        "parameter": 128,
        "move": 67,
        "score": 4,
        "nodes": 2629,
        "all_equal": true
      },
      {
        "fixture": "random-16",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 66,
        "score": 1000000,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-16",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 66,
        "score": 1000000,
        "nodes": 62,
        "all_equal": true
      },
      {
        "fixture": "random-16",
        "algorithm": "mc",
        "parameter": 128,
        "move": 66,
        "score": 16,
        "nodes": 1285,
        "all_equal": true
      },
      {
        "fixture": "random-17",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 16,
        "score": 1000000,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-17",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 59,
        "score": 1000000,
        "nodes": 55,
        "all_equal": true
      },
      {
        "fixture": "random-17",
        "algorithm": "mc",
        "parameter": 128,
        "move": 16,
        "score": 16,
        "nodes": 807,
        "all_equal": true
      },
      {
        "fixture": "random-18",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 40,
        "score": 24,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-18",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 40,
        "score": 11,
        "nodes": 156,
        "all_equal": true
      },
      {
        "fixture": "random-18",
        "algorithm": "mc",
        "parameter": 128,
        "move": 40,
        "score": 10,
        "nodes": 3177,
        "all_equal": true
      },
      {
        "fixture": "random-19",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 49,
        "score": 1505,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-19",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 49,
        "score": 1000000,
        "nodes": 94,
        "all_equal": true
      },
      {
        "fixture": "random-19",
        "algorithm": "mc",
        "parameter": 128,
        "move": 33,
        "score": 12,
        "nodes": 3020,
        "all_equal": true
      },
      {
        "fixture": "random-20",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 34,
        "score": 672,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-20",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 38,
        "score": 83,
        "nodes": 216,
        "all_equal": true
      },
      {
        "fixture": "random-20",
        "algorithm": "mc",
        "parameter": 128,
        "move": 23,
        "score": 12,
        "nodes": 3147,
        "all_equal": true
      },
      {
        "fixture": "random-21",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 5,
        "score": 1000000,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-21",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 5,
        "score": 1000000,
        "nodes": 62,
        "all_equal": true
      },
      {
        "fixture": "random-21",
        "algorithm": "mc",
        "parameter": 128,
        "move": 5,
        "score": 16,
        "nodes": 1881,
        "all_equal": true
      },
      {
        "fixture": "random-22",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 24,
        "score": 1000000,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-22",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 24,
        "score": 1000000,
        "nodes": 62,
        "all_equal": true
      },
      {
        "fixture": "random-22",
        "algorithm": "mc",
        "parameter": 128,
        "move": 24,
        "score": 16,
        "nodes": 1066,
        "all_equal": true
      },
      {
        "fixture": "random-23",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 32,
        "score": 1000000,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "random-23",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 32,
        "score": 1000000,
        "nodes": 63,
        "all_equal": true
      },
      {
        "fixture": "random-23",
        "algorithm": "mc",
        "parameter": 128,
        "move": 32,
        "score": 16,
        "nodes": 1220,
        "all_equal": true
      },
      {
        "fixture": "empty",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 40,
        "score": 20,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "empty",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 40,
        "score": 44,
        "nodes": 94,
        "all_equal": true
      },
      {
        "fixture": "empty",
        "algorithm": "mc",
        "parameter": 128,
        "move": 39,
        "score": 12,
        "nodes": 3193,
        "all_equal": true
      },
      {
        "fixture": "win-now",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 41,
        "score": 1000000,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "win-now",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 41,
        "score": 1000000,
        "nodes": 71,
        "all_equal": true
      },
      {
        "fixture": "win-now",
        "algorithm": "mc",
        "parameter": 128,
        "move": 41,
        "score": 16,
        "nodes": 2556,
        "all_equal": true
      },
      {
        "fixture": "block-now",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 41,
        "score": -28,
        "nodes": 12,
        "all_equal": true
      },
      {
        "fixture": "block-now",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 41,
        "score": -68,
        "nodes": 50,
        "all_equal": true
      },
      {
        "fixture": "block-now",
        "algorithm": "mc",
        "parameter": 128,
        "move": 41,
        "score": -4,
        "nodes": 3009,
        "all_equal": true
      },
      {
        "fixture": "one-empty",
        "algorithm": "greedy",
        "parameter": 0,
        "move": 40,
        "score": 0,
        "nodes": 1,
        "all_equal": true
      },
      {
        "fixture": "one-empty",
        "algorithm": "minimax",
        "parameter": 3,
        "move": 40,
        "score": 0,
        "nodes": 1,
        "all_equal": true
      },
      {
        "fixture": "one-empty",
        "algorithm": "mc",
        "parameter": 128,
        "move": 40,
        "score": 0,
        "nodes": 128,
        "all_equal": true
      },
      {
        "fixture": "full-draw",
        "algorithm": "greedy",
        "parameter": 0,
        "move": -1,
        "score": 0,
        "nodes": 0,
        "all_equal": true
      },
      {
        "fixture": "full-draw",
        "algorithm": "minimax",
        "parameter": 3,
        "move": -1,
        "score": 0,
        "nodes": 0,
        "all_equal": true
      },
      {
        "fixture": "full-draw",
        "algorithm": "mc",
        "parameter": 128,
        "move": -1,
        "score": 0,
        "nodes": 0,
        "all_equal": true
      }
    ]
  },
  "timing": {
    "cpython": {
      "greedy": {
        "compute_ms": {
          "samples": 120,
          "median_ms": 0.406,
          "p95_ms": 0.618,
          "min_ms": 0.256,
          "max_ms": 1.023
        },
        "roundtrip_ms": {
          "samples": 120,
          "median_ms": 0.449156,
          "p95_ms": 0.704391,
          "min_ms": 0.286369,
          "max_ms": 1.141127
        }
      },
      "minimax": {
        "compute_ms": {
          "samples": 120,
          "median_ms": 7.905,
          "p95_ms": 16.622,
          "min_ms": 1.18,
          "max_ms": 21.24
        },
        "roundtrip_ms": {
          "samples": 120,
          "median_ms": 7.993251000000001,
          "p95_ms": 16.857919,
          "min_ms": 1.214939,
          "max_ms": 21.344368
        }
      },
      "mc": {
        "compute_ms": {
          "samples": 120,
          "median_ms": 5.667,
          "p95_ms": 9.577,
          "min_ms": 0.644,
          "max_ms": 16.66
        },
        "roundtrip_ms": {
          "samples": 120,
          "median_ms": 5.7465980000000005,
          "p95_ms": 9.719944,
          "min_ms": 0.694482,
          "max_ms": 16.835643
        }
      }
    },
    "codon": {
      "greedy": {
        "compute_ms": {
          "samples": 120,
          "median_ms": 0.022,
          "p95_ms": 0.033,
          "min_ms": 0.012,
          "max_ms": 0.054
        },
        "roundtrip_ms": {
          "samples": 120,
          "median_ms": 0.0422395,
          "p95_ms": 0.085753,
          "min_ms": 0.023744,
          "max_ms": 0.103963
        }
      },
      "minimax": {
        "compute_ms": {
          "samples": 120,
          "median_ms": 0.29,
          "p95_ms": 0.709,
          "min_ms": 0.045,
          "max_ms": 0.852
        },
        "roundtrip_ms": {
          "samples": 120,
          "median_ms": 0.31719200000000003,
          "p95_ms": 0.76988,
          "min_ms": 0.055114,
          "max_ms": 0.943535
        }
      },
      "mc": {
        "compute_ms": {
          "samples": 120,
          "median_ms": 0.3035,
          "p95_ms": 0.595,
          "min_ms": 0.045,
          "max_ms": 1.82
        },
        "roundtrip_ms": {
          "samples": 120,
          "median_ms": 0.341205,
          "p95_ms": 0.676675,
          "min_ms": 0.060528,
          "max_ms": 1.892041
        }
      }
    },
    "cpp": {
      "greedy": {
        "compute_ms": {
          "samples": 120,
          "median_ms": 0.019,
          "p95_ms": 0.027,
          "min_ms": 0.011,
          "max_ms": 0.045
        },
        "roundtrip_ms": {
          "samples": 120,
          "median_ms": 0.041806499999999996,
          "p95_ms": 0.069659,
          "min_ms": 0.024834,
          "max_ms": 0.0963
        }
      },
      "minimax": {
        "compute_ms": {
          "samples": 120,
          "median_ms": 0.2275,
          "p95_ms": 0.457,
          "min_ms": 0.038,
          "max_ms": 0.671
        },
        "roundtrip_ms": {
          "samples": 120,
          "median_ms": 0.2720705,
          "p95_ms": 0.50504,
          "min_ms": 0.057818,
          "max_ms": 0.779289
        }
      },
      "mc": {
        "compute_ms": {
          "samples": 120,
          "median_ms": 0.2725,
          "p95_ms": 0.441,
          "min_ms": 0.037,
          "max_ms": 0.555
        },
        "roundtrip_ms": {
          "samples": 120,
          "median_ms": 0.2989385,
          "p95_ms": 0.512112,
          "min_ms": 0.057609,
          "max_ms": 0.61792
        }
      }
    }
  },
  "startup": {
    "cpython": {
      "samples": 20,
      "median_ms": 35.4022055,
      "p95_ms": 53.164473,
      "min_ms": 22.762995,
      "max_ms": 59.790961
    },
    "codon": {
      "samples": 20,
      "median_ms": 82.601178,
      "p95_ms": 115.58512,
      "min_ms": 55.116252,
      "max_ms": 151.149383
    },
    "cpp": {
      "samples": 20,
      "median_ms": 2.1905755,
      "p95_ms": 3.01656,
      "min_ms": 1.884491,
      "max_ms": 11.311201
    }
  },
  "memory": {
    "cpython": {
      "peak_rss_mib": 11.20703125,
      "scope": "whole persistent process across all algorithms"
    },
    "codon": {
      "peak_rss_mib": 8.9140625,
      "scope": "whole persistent process across all algorithms"
    },
    "cpp": {
      "peak_rss_mib": 3.51953125,
      "scope": "whole persistent process across all algorithms"
    }
  },
  "build": {
    "codon": {
      "seconds": 20.331666422999998,
      "exit_code": 0
    },
    "cpp": {
      "seconds": 1.2863065099999993,
      "exit_code": 0
    }
  },
  "limits": {
    "turn_wall_ms": null,
    "memory_mib": null,
    "note": "Lab runner has a 10 s response watchdog, not competition limits."
  },
  "failures": {
    "timeouts": 0,
    "crashes": 0,
    "response_mismatches": 0,
    "ott_legality": null
  },
  "limitations": [
    "Synthetic connect-five, not OTTv2.",
    "Laptop WSL measurements; not server estimates.",
    "Per-turn wall time, not CPU time.",
    "Warm-cache startup; no cold-start measurement.",
    "No uploaded team source was executed."
  ]
};
