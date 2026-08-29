#!/bin/bash

# Quick Start Script for Enhanced Video Cloning Pipeline
# This script helps you test and configure the pipeline for 1-2 minute videos

set -e

COLOR_GREEN='\033[0;32m'
COLOR_BLUE='\033[0;34m'
COLOR_YELLOW='\033[1;33m'
COLOR_RED='\033[0;31m'
COLOR_RESET='\033[0m'

echo -e "${COLOR_BLUE}"
echo "=========================================="
echo "  Video Cloning Pipeline - Quick Start"
echo "=========================================="
echo -e "${COLOR_RESET}"

# Check if we're in the right directory
if [ ! -f "config.py" ]; then
    echo -e "${COLOR_RED}Error: Please run this script from the Backend_pipeline directory${COLOR_RESET}"
    exit 1
fi

# Function to print section headers
print_section() {
    echo -e "\n${COLOR_GREEN}>>> $1${COLOR_RESET}\n"
}

# Function to print info
print_info() {
    echo -e "${COLOR_BLUE}ℹ $1${COLOR_RESET}"
}

# Function to print warning
print_warning() {
    echo -e "${COLOR_YELLOW}⚠ $1${COLOR_RESET}"
}

# Function to print success
print_success() {
    echo -e "${COLOR_GREEN}✓ $1${COLOR_RESET}"
}

# Menu
echo "What would you like to do?"
echo ""
echo "1) Run full test suite"
echo "2) Test with a specific video"
echo "3) View current configuration"
echo "4) View documentation"
echo "5) Check dependencies"
echo "6) Run quick validation"
echo "7) Exit"
echo ""

read -p "Enter your choice (1-7): " choice

case $choice in
    1)
        print_section "Running Full Test Suite"
        
        # Check for test video
        if [ ! -f "test_video.mp4" ]; then
            print_warning "No test_video.mp4 found in current directory"
            read -p "Enter path to test video: " video_path
        else
            video_path="test_video.mp4"
        fi
        
        if [ -f "$video_path" ]; then
            print_info "Testing with: $video_path"
            python test_long_video_pipeline.py "$video_path" --test all
        else
            print_warning "Video file not found: $video_path"
            exit 1
        fi
        ;;
        
    2)
        print_section "Test with Specific Video"
        
        read -p "Enter path to video file: " video_path
        
        if [ ! -f "$video_path" ]; then
            print_warning "Video file not found: $video_path"
            exit 1
        fi
        
        echo ""
        echo "Select test to run:"
        echo "1) All tests"
        echo "2) Duration check"
        echo "3) Audio extraction"
        echo "4) Chunking test"
        echo "5) Alignment test"
        echo "6) Gap detection"
        echo "7) Quality optimization"
        
        read -p "Enter choice (1-7): " test_choice
        
        case $test_choice in
            1) test_flag="all" ;;
            2) test_flag="duration" ;;
            3) test_flag="audio" ;;
            4) test_flag="chunking" ;;
            5) test_flag="alignment" ;;
            6) test_flag="gaps" ;;
            7) test_flag="quality" ;;
            *) print_warning "Invalid choice"; exit 1 ;;
        esac
        
        python test_long_video_pipeline.py "$video_path" --test "$test_flag"
        ;;
        
    3)
        print_section "Current Configuration"
        python config.py
        print_info "To modify settings, edit config.py"
        ;;
        
    4)
        print_section "Documentation"
        
        echo "Available documentation:"
        echo ""
        echo "1) Pipeline Improvements Summary"
        echo "2) Long Video Processing Guide"
        echo "3) Configuration Reference (config.py)"
        echo ""
        
        read -p "Which document would you like to view? (1-3): " doc_choice
        
        case $doc_choice in
            1) 
                if command -v bat &> /dev/null; then
                    bat PIPELINE_IMPROVEMENTS_SUMMARY.md
                elif command -v less &> /dev/null; then
                    less PIPELINE_IMPROVEMENTS_SUMMARY.md
                else
                    cat PIPELINE_IMPROVEMENTS_SUMMARY.md
                fi
                ;;
            2) 
                if command -v bat &> /dev/null; then
                    bat LONG_VIDEO_GUIDE.md
                elif command -v less &> /dev/null; then
                    less LONG_VIDEO_GUIDE.md
                else
                    cat LONG_VIDEO_GUIDE.md
                fi
                ;;
            3) 
                if command -v bat &> /dev/null; then
                    bat config.py
                elif command -v less &> /dev/null; then
                    less config.py
                else
                    cat config.py
                fi
                ;;
            *) print_warning "Invalid choice" ;;
        esac
        ;;
        
    5)
        print_section "Checking Dependencies"
        
        # Check Python
        if command -v python &> /dev/null; then
            python_version=$(python --version 2>&1)
            print_success "Python: $python_version"
        else
            print_warning "Python not found"
        fi
        
        # Check FFmpeg
        if command -v ffmpeg &> /dev/null; then
            ffmpeg_version=$(ffmpeg -version 2>&1 | head -n 1)
            print_success "FFmpeg: $ffmpeg_version"
        else
            print_warning "FFmpeg not found - REQUIRED"
        fi
        
        # Check FFprobe
        if command -v ffprobe &> /dev/null; then
            print_success "FFprobe: installed"
        else
            print_warning "FFprobe not found - REQUIRED"
        fi
        
        # Check Python packages
        print_info "Checking Python packages..."
        
        packages=("torch" "numpy" "scipy" "cv2" "transformers")
        
        for package in "${packages[@]}"; do
            if python -c "import $package" 2>/dev/null; then
                print_success "$package: installed"
            else
                print_warning "$package: NOT installed"
            fi
        done
        
        echo ""
        print_info "To install missing packages, run:"
        echo "pip install torch numpy scipy opencv-python transformers"
        ;;
        
    6)
        print_section "Quick Validation"
        
        print_info "Checking file structure..."
        
        required_files=(
            "config.py"
            "advanced_video_processor.py"
            "enhanced_tts.py"
            "test_long_video_pipeline.py"
            "lip_sync_generate.py"
            "app.py"
            "video_processing.py"
        )
        
        all_good=true
        
        for file in "${required_files[@]}"; do
            if [ -f "$file" ]; then
                print_success "$file"
            else
                print_warning "$file - MISSING"
                all_good=false
            fi
        done
        
        echo ""
        
        if [ "$all_good" = true ]; then
            print_success "All required files present!"
            echo ""
            print_info "Pipeline is ready. Run option 1 to test with a video."
        else
            print_warning "Some files are missing. Please check the installation."
        fi
        ;;
        
    7)
        print_info "Exiting..."
        exit 0
        ;;
        
    *)
        print_warning "Invalid choice"
        exit 1
        ;;
esac

echo ""
print_success "Done!"
echo ""
print_info "For more information, see:"
echo "  - PIPELINE_IMPROVEMENTS_SUMMARY.md"
echo "  - LONG_VIDEO_GUIDE.md"
echo ""
