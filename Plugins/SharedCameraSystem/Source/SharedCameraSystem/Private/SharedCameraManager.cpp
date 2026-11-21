// Fill out your copyright notice in the Description page of Project Settings.


#include "SharedCameraManager.h"
#include "Kismet/KismetMathLibrary.h"
#include "Engine/World.h"
#include "GameFramework/SpringArmComponent.h"
#include "Camera/CameraComponent.h"
#include "Kismet/GameplayStatics.h"
#include "Net/UnrealNetwork.h"
#include "NavigationInvokerComponent.h"

// Sets default values
ASharedCameraManager::ASharedCameraManager()
{
	PrimaryActorTick.bCanEverTick = true;

	DefaultSceneRoot = CreateDefaultSubobject<USceneComponent>(TEXT("DefaultSceneRoot"));
	RootComponent = DefaultSceneRoot;

	/** SpringArm */
	SpringArmComp = CreateDefaultSubobject<USpringArmComponent>(TEXT("SpringArmComp"));
	SpringArmComp->SetupAttachment(GetRootComponent());
	SpringArmComp->TargetArmLength = 0.f;
	SpringArmComp->bDoCollisionTest = false;
	SpringArmComp->bEnableCameraLag = true;
	SpringArmComp->CameraLagSpeed = 10.f;
	SpringArmComp->bEnableCameraRotationLag = true;
	SpringArmComp->CameraRotationLagSpeed = 5.f;

	/** Camera */
	CameraComp = CreateDefaultSubobject<UCameraComponent>(TEXT("CameraComp"));
	CameraComp->SetupAttachment(SpringArmComp);
	CameraComp->bUsePawnControlRotation = false;

	/** ReferenceScene setting */
	ReferenceScene = CreateDefaultSubobject<USceneComponent>(TEXT("ReferenceScene"));
	ReferenceScene->SetupAttachment(GetRootComponent());
	ReferenceScene->SetWorldLocation(FVector(630.f, 0.f, 0.f));

	/** Bounds */
	Bounds = CreateDefaultSubobject<UStaticMeshComponent>(TEXT("Bounds"));
	Bounds->SetupAttachment(GetRootComponent());
	Bounds->SetCollisionProfileName(FName(TEXT("InvisibleWallDynamic")));
	Bounds->SetHiddenInGame(true);

	/** Navigation Invoker */
	NavigationInvoker = CreateDefaultSubobject<UNavigationInvokerComponent>(TEXT("NavigationInvoker"));
	NavigationInvoker->SetGenerationRadii(BoundsRadius, BoundsRadius);

	/** Multiplayer */
	SetReplicates(true);
	AActor::SetReplicateMovement(true);
}

// Called when the game starts or when spawned
void ASharedCameraManager::BeginPlay()
{
	Super::BeginPlay();

	PreviousDesiredLength = SpringArmComp->TargetArmLength;

	bDefaultEnableCameraRotationLag = SpringArmComp->bEnableCameraRotationLag;

	NavigationInvoker->SetGenerationRadii(BoundsRadius, BoundsRadius);
}

void ASharedCameraManager::OnConstruction(const FTransform& Transform)
{
	Super::OnConstruction(Transform);

	Bounds->SetWorldScale3D(FVector(BoundsRadius, BoundsRadius, BoundsHalfHeight));
}

#if WITH_EDITOR
void ASharedCameraManager::PostEditChangeProperty(FPropertyChangedEvent& PropertyChangedEvent)
{
	Super::PostEditChangeProperty(PropertyChangedEvent);

	NavigationInvoker->SetGenerationRadii(BoundsRadius, BoundsRadius);

	if (PropertyChangedEvent.GetPropertyName() == FName(TEXT("MinSpringArmRotation")))
		MinSpringArmRotation = FMath::Clamp(MinSpringArmRotation, 0, MaxSpringArmRotation);
	if (PropertyChangedEvent.GetPropertyName() == FName(TEXT("MaxSpringArmRotation")))
		MaxSpringArmRotation = FMath::Clamp(MaxSpringArmRotation, MinSpringArmRotation, UE_BIG_NUMBER);

	if (PropertyChangedEvent.GetPropertyName() == FName(TEXT("MinGeneralOverlapDistance")))
		MinGeneralOverlapDistance = FMath::Clamp(MinGeneralOverlapDistance, 0, MaxGeneralOverlapDistance);
	if (PropertyChangedEvent.GetPropertyName() == FName(TEXT("MaxGeneralOverlapDistance")))
		MaxGeneralOverlapDistance = FMath::Clamp(MaxGeneralOverlapDistance, MinGeneralOverlapDistance, UE_BIG_NUMBER);

	if (PropertyChangedEvent.GetPropertyName() == FName(TEXT("MinMeleeOverlapDistance")))
		MinMeleeOverlapDistance = FMath::Clamp(MinMeleeOverlapDistance, 0, MaxMeleeOverlapDistance);
	if (PropertyChangedEvent.GetPropertyName() == FName(TEXT("MaxMeleeOverlapDistance")))
		MaxMeleeOverlapDistance = FMath::Clamp(MaxMeleeOverlapDistance, MinMeleeOverlapDistance, UE_BIG_NUMBER);
}
#endif


// Called every frame
void ASharedCameraManager::Tick(float DeltaTime)
{
	Super::Tick(DeltaTime);

	// A death destroys the pawn and the GameMode spawns a fresh one, so the destroyed actor stays in
	// the array and its replacement is appended behind it. Prune before choosing the pair, or the
	// framing locks onto dead actors and dereferences null once GC clears the slot.
	Players.RemoveAll([](const auto& Player) { return !IsValid(Player); });

	if (Players.Num() >= 2) CalculateVal();
}

void ASharedCameraManager::CalculateVal()
{
	SetCameraPosition();

	/** Rotate the DefaultScene or adjust the ReferenceScene, if the SharedCameraType matches */
	if (GetSharedCameraType() == ESharedCameraType::ECT_3D || GetSharedCameraType() == ESharedCameraType::ECT_2_5D)
	{
		SetP1RelativeVal();

		if (GetSharedCameraType() == ESharedCameraType::ECT_3D)
		{
			SetReferenceScene();
			SetViewAllPlayers();

			if (Debug)
			{
				DrawDebugSphere(GetWorld(), ReferenceScene->GetComponentLocation(), 10.f, 10.f, FColor::Green);

				FVector RefToRoot_Dir = UKismetMathLibrary::GetDirectionUnitVector(
					ReferenceScene->GetComponentLocation(), DefaultSceneRoot->GetComponentLocation());

				DrawDebugDirectionalArrow(GetWorld(), Players[0]->GetActorLocation(),
				                          Players[0]->GetActorLocation() + (RefToRoot_Dir * P1ToP2_Distance), 10.f,
				                          FColor::Red);
				DrawDebugDirectionalArrow(GetWorld(), Players[1]->GetActorLocation(),
				                          Players[1]->GetActorLocation() - (RefToRoot_Dir * P1ToP2_Distance), 10.f,
				                          FColor::Red);
				DrawDebugSphere(GetWorld(), Players[0]->GetActorLocation() + (RefToRoot_Dir * P1ToP2_Distance), 10.f,
				                10.f, FColor::Blue);
				DrawDebugSphere(GetWorld(), Players[1]->GetActorLocation() - (RefToRoot_Dir * P1ToP2_Distance), 10.f,
				                10.f, FColor::Blue);
			}
		}
		RotateDefaultScene();
	}

	if (Debug)
	{
		DrawDebugSphere(GetWorld(), DefaultSceneRoot->GetComponentLocation(), 10.f, 10.f, FColor::Red);
		DrawDebugSphere(GetWorld(), GetRelativeCameraPosition(), 10.f, 10.f, FColor::Black);
	}
}

void ASharedCameraManager::SetCameraPosition()
{
	/** Positioning of the DefaultSceneRoot */
	FVector DefaultLocation = (Players[0]->GetActorLocation() + Players[1]->GetActorLocation()) / 2.f;
	DefaultLocation.Z += Height;
	DefaultSceneRoot->SetWorldLocation(DefaultLocation);

	/** Specify the value of the GlobalDistanceFactor (P1 to P2) */
	float P1ToP2Distance = (Players[1]->GetActorLocation() - Players[0]->GetActorLocation()).Size() / 1200.f;
	GlobalDistanceFactor = UKismetMathLibrary::FClamp(P1ToP2Distance, 0.f, 1.f);

	/** Adjust the Pitch on the SpringArm according to the distance */
	float SpringPitch = UKismetMathLibrary::Lerp(MaxSpringArmRotation, MinSpringArmRotation, GlobalDistanceFactor) * -
		1.f;
	SpringArmComp->SetRelativeRotation(FRotator(SpringPitch, 0.f, 0.f));

	/** Specify the length of the SpringArm */
	FVector P1ToP2Vector = Players[1]->GetActorLocation() - Players[0]->GetActorLocation();
	P1ToP2Vector.Z = 0;
	float P1ToP2CenterLength = (P1ToP2Vector / 2.f).Size();
	float SpringArmLength = UKismetMathLibrary::Lerp(SpringArmBaseLength,
	                                                 (SpringArmBaseLength + SpringArmExtraLength),
	                                                 GlobalDistanceFactor);

	float DesiredLength = P1ToP2CenterLength + SpringArmLength;
	SetSpringArmLength(DesiredLength);
}

void ASharedCameraManager::SetSpringArmLength(float DesiredLength)
{
	const float DeltaTime = UGameplayStatics::GetWorldDeltaSeconds(this);

	if (DeltaTime > UE_KINDA_SMALL_NUMBER)
	{
		const float ArmMovementStep = (DesiredLength - PreviousDesiredLength) * (1.f / DeltaTime);
		float LerpTarget = PreviousDesiredLength;
		float LagSpeed;
		float InterpSpeed;

		LerpTarget += ArmMovementStep * DeltaTime;

		if (bIsMeleeAttacking && ((Players[0]->GetActorLocation() - Players[1]->GetActorLocation()).Size()) <
			MeleeCameraRotateDistance)
		{
			LagSpeed = MeleeSpringArmLengthLagSpeed;
			InterpSpeed = MeleeSpringArmLengthInterpSpeed;
		}
		else
		{
			LagSpeed = SpringArmLengthLagSpeed;
			InterpSpeed = SpringArmLengthInterpSpeed;
		}

		if (LagSpeed != PreviousLagSpeed)
			LagSpeed = FMath::FInterpTo<float>(PreviousLagSpeed, LagSpeed, DeltaTime, InterpSpeed);

		DesiredLength = FMath::FInterpTo<float>(PreviousDesiredLength, LerpTarget, DeltaTime, LagSpeed);
		PreviousDesiredLength = DesiredLength;
		PreviousLagSpeed = LagSpeed;

		SpringArmComp->TargetArmLength = DesiredLength;
	}
}

void ASharedCameraManager::SetP1RelativeVal()
{
	P1ToP2_Distance = UKismetMathLibrary::VSizeXY(Players[0]->GetActorLocation() - Players[1]->GetActorLocation());

	if (GetSharedCameraType() == ESharedCameraType::ECT_3D)
	{
		const FVector RefToRoot_Dir = UKismetMathLibrary::GetDirectionUnitVector(ReferenceScene->GetComponentLocation(),
			DefaultSceneRoot->GetComponentLocation());
		OverlapDistance = UKismetMathLibrary::Vector_Distance2D(
			Players[0]->GetActorLocation() + (RefToRoot_Dir * P1ToP2_Distance), Players[1]->GetActorLocation());
	}
	else if (GetSharedCameraType() == ESharedCameraType::ECT_2_5D)
	{
		FVector P1ToRoot_DirectionVec = UKismetMathLibrary::GetDirectionUnitVector(
			Players[0]->GetActorLocation(), DefaultSceneRoot->GetComponentLocation());
		P1ToRoot_Dot = UKismetMathLibrary::Dot_VectorVector(DefaultSceneRoot->GetRightVector(),
		                                                    P1ToRoot_DirectionVec);
	}

	/** Identify the left(1) and right(-1) positions of P1 based on the ReferenceScene */
	FVector P1ToRef_DirectionVec = UKismetMathLibrary::GetDirectionUnitVector(
		Players[0]->GetActorLocation(), ReferenceScene->GetComponentLocation());
	float P1ToRef_Dot = UKismetMathLibrary::Dot_VectorVector(ReferenceScene->GetRightVector(), P1ToRef_DirectionVec);
	IsLeft = (P1ToRef_Dot <= 0.f) ? -1 : 1;


	float P1ToCamera_Distance = (Players[0]->GetActorLocation() - GetRelativeCameraPosition()).Size();
	float P2ToCamera_Distance = (Players[1]->GetActorLocation() - GetRelativeCameraPosition()).Size();
	IsForward = (P2ToCamera_Distance > P1ToCamera_Distance) ? 1 : -1;
}

FVector ASharedCameraManager::GetRelativeCameraPosition() const
{
	return SpringArmComp->GetComponentLocation() + SpringArmComp->GetForwardVector() * -1 *
		SpringArmComp->TargetArmLength;
}

void ASharedCameraManager::SetReferenceScene()
{
	float P1ToCamera_Distance = (Players[0]->GetActorLocation() - GetRelativeCameraPosition()).Size();
	float P2ToCamera_Distance = (Players[1]->GetActorLocation() - GetRelativeCameraPosition()).Size();

	/** Place the X-axis of the ReferenceScene around the player closer to the camera */
	FVector Vec = (P2ToCamera_Distance > P1ToCamera_Distance)
		              ? FVector((P1ToP2_Distance / 2) * -1.f, 0.f, 0.f)
		              : FVector((P1ToP2_Distance / 2), 0.f, 0.f);
	ReferenceScene->SetRelativeLocation(Vec);
}

void ASharedCameraManager::SetViewAllPlayers()
{
	/** If either player is attacking, check overlap */
	if (bIsMeleeAttacking && ((Players[0]->GetActorLocation() - Players[1]->GetActorLocation()).Size()) <
		MeleeCameraRotateDistance)
	{
		if (OverlapDistance < MinMeleeOverlapDistance && !bArePlayersOverlapping && !bIsEdgeRotating)
			bArePlayersOverlapping =
				true;

		/** Activate SetNonOverlap() if Overlap */
		if (bArePlayersOverlapping) SetNonOverlap();
	}
	/** If neither player has movement, check overlap */
	else if (UKismetMathLibrary::VSizeXY(Players[0]->GetVelocity()) <= 0.1f && UKismetMathLibrary::VSizeXY(
		Players[1]->GetVelocity()) <= 0.1f)
	{
		if (OverlapDistance < MinGeneralOverlapDistance && !bArePlayersOverlapping && !bIsEdgeRotating)
			bArePlayersOverlapping =
				true;

		/** Activate SetNonOverlap() if Overlap */
		if (bArePlayersOverlapping) SetNonOverlap();
	}
	else
	{
		bArePlayersOverlapping = false;
		NumOverlapTicks = 0;
		SpringArmComp->bEnableCameraRotationLag = bDefaultEnableCameraRotationLag;
	}
}

void ASharedCameraManager::SetNonOverlap()
{
	float SmoothFactor = 1.f, OverlapDistanceFactor = 1.f;
	float OverlapRotationForce, MaxOverlapDistance, MinOverlapDistance;

	NumOverlapTicks += 1;

	if (bIsMeleeAttacking && ((Players[0]->GetActorLocation() - Players[1]->GetActorLocation()).Size()) <
		MeleeCameraRotateDistance)
	{
		OverlapRotationForce = MeleeOverlapRotationForceFactor;
		MaxOverlapDistance = MaxMeleeOverlapDistance;
		MinOverlapDistance = MinMeleeOverlapDistance;

		if (bUseCurveSmoothing)
		{
			if (IsValid(MeleeOverlapRotationCurve))
			{
				SmoothFactor = MeleeOverlapRotationCurve->GetFloatValue(
					NumOverlapTicks * UGameplayStatics::GetWorldDeltaSeconds(this));
			}
			else
			{
				UE_LOG(LogTemp, Warning, TEXT("No valid Melee Overlap Rotation Curve found!"));
			}
		}

		SpringArmComp->bEnableCameraRotationLag = bDefaultEnableCameraRotationLag;
	}
	else
	{
		OverlapRotationForce = GeneralOverlapRotationForceFactor;
		MaxOverlapDistance = MaxGeneralOverlapDistance;
		MinOverlapDistance = MinGeneralOverlapDistance;

		if (bUseCurveSmoothing)
		{
			if (IsValid(GeneralOverlapRotationCurve))
			{
				SmoothFactor = GeneralOverlapRotationCurve->GetFloatValue(
					NumOverlapTicks * UGameplayStatics::GetWorldDeltaSeconds(this));
			}
			else
			{
				UE_LOG(LogTemp, Warning, TEXT("No valid General Overlap Rotation Curve found!"));
			}
		}

		SpringArmComp->bEnableCameraRotationLag = bUseCameraRotationLagDuringOverlap;
	}

	if (IsValid(OverlapDistanceFactorCurve))
	{
		OverlapDistanceFactor = OverlapDistanceFactorCurve->GetFloatValue(P1ToP2_Distance);
		//UE_LOG(LogTemp, Log, TEXT("%f"), OverlapDistanceFactor)
	}
	else
	{
		UE_LOG(LogTemp, Warning, TEXT("No valid Overlap Distance Factor Curve found!"));
	}

	MaxOverlapDistance *= OverlapDistanceFactor;
	MinOverlapDistance *= OverlapDistanceFactor;

	float Force = FMath::Pow(
		UKismetMathLibrary::FClamp(P1ToP2_Distance / 30.f, 0.f, 1.f),
		2.f) * OverlapRotationForce * (OverlapDistance - MaxOverlapDistance) / P1ToP2_Distance;

	float YawForce = ((IsForward * IsLeft) * (Force)) * UGameplayStatics::GetWorldDeltaSeconds(this) * SmoothFactor;

	DefaultSceneRoot->AddWorldRotation(FRotator(0.f, YawForce, 0.f), false);

	if (OverlapDistance >= MaxOverlapDistance || bIsEdgeRotating)
	{
		bArePlayersOverlapping = false;
		NumOverlapTicks = 0;
		SpringArmComp->bEnableCameraRotationLag = bDefaultEnableCameraRotationLag;
	}
}

void ASharedCameraManager::RotateDefaultScene()
{
	if (GetSharedCameraType() == ESharedCameraType::ECT_3D)
	{
		float P1ToReference_Length = UKismetMathLibrary::VSizeXY(
			Players[0]->GetActorLocation() - ReferenceScene->GetComponentLocation());

		/** If the P1ToReference_Length is bigger than EdgeRotationDistance, rotate DefaultSceneRoot */
		if (P1ToReference_Length >= EdgeRotationDistance && !bArePlayersOverlapping)
		{
			/** The strength of the force increases with distance. */
			bIsEdgeRotating = true;
			float RotationDelayScaledByOffset = ScaleRotationDelayByEdgeRotationOffset
				                                    ? UKismetMathLibrary::FClamp(
					                                    (RotationDelay - (P1ToReference_Length / 267.f)), 1,
					                                    RotationDelay)
				                                    : RotationDelay;
			float EdgeRotationFactorByDistance = ScaleEdgeRotationForceByDistance
				                                     ? UKismetMathLibrary::FClamp(500.f / P1ToP2_Distance, 1, 2)
				                                     : 1.f;

			float Force = EdgeRotationForceFactor * EdgeRotationFactorByDistance * (P1ToReference_Length -
				EdgeRotationDistance) / RotationDelayScaledByOffset;
			float YawForce = ((IsForward * IsLeft) * Force) * UGameplayStatics::GetWorldDeltaSeconds(this);
			DefaultSceneRoot->AddWorldRotation(FRotator(0.f, YawForce, 0.f), false);
		}
		else bIsEdgeRotating = false;
	}
	else
	{
		float AbsInnerVal = FMath::Abs(P1ToRoot_Dot);
		UE_LOG(LogTemp, Display, TEXT("Yaw: %f"), AbsInnerVal);
		if (AbsInnerVal <= 0.98f)
		{
			float Force = (0.98f - AbsInnerVal) * RotateForce * FMath::Pow(
				UKismetMathLibrary::FClamp(P1ToP2_Distance / 100.f, 0.f, 1.f), 3.f);
			float YawForce = ((-IsForward * IsLeft) * Force) * UGameplayStatics::GetWorldDeltaSeconds(this);
			DefaultSceneRoot->AddWorldRotation(FRotator(0.f, YawForce, 0.f), false);
		}
	}
}

void ASharedCameraManager::SetPlayer(AActor* Player)
{
	if (!HasAuthority()) ServerSetPlayer(Player);
	Players.Push(Player);
}

void ASharedCameraManager::ServerSetPlayer_Implementation(AActor* Player)
{
	SetPlayer(Player);
}

bool ASharedCameraManager::ServerSetPlayer_Validate(AActor* Player)
{
	return true;
}

void ASharedCameraManager::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);

	DOREPLIFETIME(ASharedCameraManager, Players);
}
